import os
import subprocess
import sys

def main():
    print("🚀 开始打包流程...")
    
    # 1. 运行 PyInstaller
    print("\n📦 [1/2] 正在使用 PyInstaller 编译可执行文件...")
    spec_file = "UTAR_WBLE_Agent.spec"
    
    if not os.path.exists(spec_file):
        print(f"❌ 找不到 {spec_file}，请确保在项目根目录运行此脚本。")
        sys.exit(1)
        
    try:
        subprocess.run([sys.executable, "-m", "PyInstaller", "--noconfirm", spec_file], check=True)
    except subprocess.CalledProcessError:
        print("❌ PyInstaller 编译失败！")
        sys.exit(1)
        
    print("✅ PyInstaller 编译成功，已生成 dist/UTAR_WBLE_Agent.exe")
    
    # 2. 运行 Inno Setup
    print("\n💿 [2/2] 正在使用 Inno Setup 制作安装包...")
    iss_file = "setup.iss"
    
    # Auto-detect Inno Setup Compiler path
    possible_paths = [
        r"C:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        r"C:\Program Files\Inno Setup 6\ISCC.exe",
        r"C:\Program Files (x86)\Inno Setup 5\ISCC.exe",
        r"C:\Program Files\Inno Setup 5\ISCC.exe",
        r"D:\Program Files (x86)\Inno Setup 6\ISCC.exe",
        r"D:\Program Files\Inno Setup 6\ISCC.exe"
    ]
    
    iscc_path = None
    for p in possible_paths:
        if os.path.exists(p):
            iscc_path = p
            break
            
    if not iscc_path:
        print("⚠️ 找不到 Inno Setup 编译器 (ISCC.exe)。请确认是否已安装 Inno Setup。")
        print("请手动打开 setup.iss 并点击绿色的运行按钮进行打包。")
        sys.exit(0)
        
    try:
        subprocess.run([iscc_path, iss_file], check=True)
    except subprocess.CalledProcessError:
        print("❌ Inno Setup 制作安装包失败！")
        sys.exit(1)
        
    print("\n🎉 全部完成！你的安装包已经生成在 release/ 文件夹中了！")
    print(f"你可以去看看: {os.path.abspath('release')}")

if __name__ == "__main__":
    main()
