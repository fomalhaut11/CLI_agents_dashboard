// pty-bridge.js — 单终端 node-pty 桥接 (供 Python _Term 调用, 替代 winpty)
//
// 用 node-pty 捆绑的新版 ConPTY (useConptyDll) 启动 shell, 解决 Win10 旧系统 ConPTY
// 的 scrollback 缺陷 (全屏 TUI 如 claude/codex 滚不到历史)。
//
// 进程间协议:
//   stdout  : PTY 原始字节流 (二进制, 直接转发给浏览器 xterm)。本脚本绝不向 stdout 打日志。
//   stdin   : 帧 = [type:1][len:4 大端][payload]
//             type 'i'(0x69)=键盘输入(payload=utf8); 'r'(0x72)=resize(payload="cols,rows")
//   stderr  : 仅用于诊断日志
//
// argv: node pty-bridge.js <cols> <rows> <cwd> <shellFile> [shellArg...]

const pty = require("node-pty");

const cols = parseInt(process.argv[2] || "100", 10);
const rows = parseInt(process.argv[3] || "30", 10);
const cwd = process.argv[4] || process.env.USERPROFILE || process.cwd();
const shellFile = process.argv[5] || "powershell.exe";
const shellArgs = process.argv.slice(6);

let proc;
try {
  proc = pty.spawn(shellFile, shellArgs, {
    name: "xterm-256color",
    cols, rows, cwd,
    env: process.env,
    useConpty: true,
    useConptyDll: true,          // 关键: 用捆绑的新版 conpty.dll, 修好 scrollback
  });
} catch (e) {
  process.stderr.write("[pty-bridge] spawn failed: " + (e && e.message) + "\n");
  process.exit(11);
}

// 让 Python 拿到真实 shell 子进程 pid (用于进程树状态扫描): 首行 stderr 报告
process.stderr.write("[pty-bridge] pid=" + proc.pid + "\n");

// PTY 输出 -> stdout (node-pty 默认 utf8 解码且不会切断多字节; 这里按 utf8 写回字节)
proc.onData((d) => {
  try { process.stdout.write(Buffer.from(d, "utf8")); } catch (e) {}
});
proc.onExit(({ exitCode }) => {
  try { process.stdout.end(); } catch (e) {}
  process.exit(typeof exitCode === "number" ? exitCode : 0);
});

// stdin 帧解析
let buf = Buffer.alloc(0);
process.stdin.on("data", (chunk) => {
  buf = Buffer.concat([buf, chunk]);
  while (buf.length >= 5) {
    const type = buf[0];
    const len = buf.readUInt32BE(1);
    if (buf.length < 5 + len) break;
    const payload = buf.slice(5, 5 + len);
    buf = buf.slice(5 + len);
    if (type === 0x69) {                       // 'i' 输入
      try { proc.write(payload.toString("utf8")); } catch (e) {}
    } else if (type === 0x72) {                // 'r' resize
      const parts = payload.toString("utf8").split(",");
      const c = parseInt(parts[0], 10), r = parseInt(parts[1], 10);
      if (c > 0 && r > 0) { try { proc.resize(c, r); } catch (e) {} }
    }
  }
});
process.stdin.on("end", () => { try { proc.kill(); } catch (e) {} process.exit(0); });
process.on("SIGTERM", () => { try { proc.kill(); } catch (e) {} process.exit(0); });
