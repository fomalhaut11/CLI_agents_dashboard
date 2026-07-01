// term-sidecar: node-pty + ConPTY 终端后端 (stream_dashboard 路线1 验证用)
//
// 目的: 用 ConPTY (node-pty 在 Win10>=1809 默认走 ConPTY) 替代旧的 winpty 后端,
// 让 Claude/Codex 这类全屏 TUI 的重绘忠实透传. 协议与现有 dashboard 一致:
//   client -> server: 文本 JSON 帧 {i:"输入"} 或 {r:[cols,rows]}
//   server -> client: 二进制帧 (raw PTY 字节, 喂 xterm.js)
// 持久终端: tid 复用, ws 断开只 detach 不 kill; 附着时回放缓冲重绘屏幕.

const http = require("http");
const fs = require("fs");
const path = require("path");
const urlmod = require("url");
const { WebSocketServer } = require("ws");
const pty = require("node-pty");

const PORT = parseInt(process.env.TERM_SIDECAR_PORT || "5113", 10);
const HOST = "127.0.0.1";
const SHELL = "powershell.exe";
const SHELL_ARGS = ["-NoLogo"];
const DEFAULT_CWD = process.env.TERM_SIDECAR_CWD || "F:\\zx\\multifactors_beta";
const SCROLLBACK = 200000; // 回放缓冲字符上限
const TID_RE = /^[0-9a-zA-Z_-]{1,64}$/;

const TERMS = new Map(); // tid -> { proc, buf, ws, cwd }

function getOrCreate(tid, cwd, isNew) {
  let t = TERMS.get(tid);
  if (t) return t;
  if (!isNew) return null;

  const proc = pty.spawn(SHELL, SHELL_ARGS, {
    name: "xterm-256color",
    cols: 100,
    rows: 30,
    cwd: cwd && fs.existsSync(cwd) ? cwd : DEFAULT_CWD,
    env: process.env,
    useConpty: true,     // 走 ConPTY (非 winpty)
    useConptyDll: true,  // 关键: 用 node-pty 捆绑的新版 conpty.dll(v1.23), 而非 Win10 旧系统 ConPTY
                         // 旧系统 ConPTY 在 build 19045 上 scrollback 发射有缺陷 -> 滚不到历史; 新版修了
  });
  t = { proc, buf: "", ws: null, cwd };
  console.log(`[spawn] tid=${tid} pid=${proc.pid} cwd=${cwd || DEFAULT_CWD} useConptyDll=true`);

  proc.onData((data) => {
    // node-pty 默认 utf8 解码且自行处理多字节边界, 不会切断半个字符
    let combined = t.buf + data;
    if (combined.length > SCROLLBACK) {
      const cut = combined.length - SCROLLBACK;
      const nxt = combined.indexOf("\x1b", cut); // 截断对齐到下一个 ESC, 防重连首屏花屏
      combined = combined.slice(nxt !== -1 ? nxt : cut);
    }
    t.buf = combined;
    if (t.ws && t.ws.readyState === 1) {
      try { t.ws.send(Buffer.from(data, "utf8")); } catch (e) {}
    }
  });

  proc.onExit(({ exitCode }) => {
    console.log(`[exit] tid=${tid} code=${exitCode}`);
    if (t.ws && t.ws.readyState === 1) { try { t.ws.close(); } catch (e) {} }
    TERMS.delete(tid);
  });

  TERMS.set(tid, t);
  return t;
}

// ---- 静态页 (最小 xterm 测试页) ----
const server = http.createServer((req, res) => {
  let p = urlmod.parse(req.url).pathname;
  if (p === "/" || p === "/test") p = "/test.html";
  const file = path.join(__dirname, "public", path.normalize(p).replace(/^(\.\.[\\/])+/, ""));
  fs.readFile(file, (err, data) => {
    if (err) { res.writeHead(404); res.end("not found"); return; }
    const ct = file.endsWith(".html") ? "text/html; charset=utf-8" : "application/octet-stream";
    res.writeHead(200, { "Content-Type": ct });
    res.end(data);
  });
});

// ---- PTY websocket ----
const wss = new WebSocketServer({ server, path: "/pty" });
wss.on("connection", (ws, req) => {
  const q = urlmod.parse(req.url, true).query;
  const tid = (q.term || "").toString();
  const cwd = (q.cwd || "").toString();
  const isNew = q.new === "1";
  if (!TID_RE.test(tid)) { ws.close(); return; }
  const t = getOrCreate(tid, cwd, isNew);
  if (!t) { ws.close(); return; } // 客户端以为存在但已不在 (服务重启过)

  // attach: 绑定新 ws, 回放历史, 踢掉旧 viewer
  const old = t.ws;
  t.ws = ws;
  try { ws.send(Buffer.from(t.buf, "utf8")); } catch (e) {}
  if (old && old !== ws) { try { old.close(); } catch (e) {} }

  ws.on("message", (msg) => {
    let obj;
    try { obj = JSON.parse(msg.toString()); } catch (e) { return; }
    if (obj && "i" in obj) {
      t.proc.write(obj.i);
    } else if (obj && "r" in obj && Array.isArray(obj.r)) {
      try { t.proc.resize(parseInt(obj.r[0], 10), parseInt(obj.r[1], 10)); } catch (e) {}
    }
  });
  ws.on("close", () => { if (t.ws === ws) t.ws = null; }); // 只 detach, 不 kill
});

server.listen(PORT, HOST, () => {
  console.log(`term-sidecar (node-pty + ConPTY) -> http://${HOST}:${PORT}  shell=${SHELL}  default_cwd=${DEFAULT_CWD}`);
});
