' AI 游戏陪玩 · 无窗口静默启动
' 双击这个文件启动，不会出现黑色控制台窗口，因此也不存在"关错窗口把程序关掉"的问题。
' 退出方式：右键任务栏托盘里的蓝色 AI 图标 -> 退出。
Option Explicit

Dim fso, shell, base, py, args
Set fso = CreateObject("Scripting.FileSystemObject")
Set shell = CreateObject("WScript.Shell")

base = fso.GetParentFolderName(WScript.ScriptFullName)
py = shell.ExpandEnvironmentStrings("%USERPROFILE%") & "\.dsh\dsh-runtimes\dsh-primary-runtime\dependencies\python\pythonw.exe"
If Not fso.FileExists(py) Then py = "pythonw.exe"

shell.CurrentDirectory = base
args = """" & py & """ """ & base & "\main.py"""

' 0 = 隐藏窗口
shell.Run args, 0, False
