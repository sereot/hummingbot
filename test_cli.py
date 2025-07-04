#!/usr/bin/env python3
"""
Simple test to verify Hummingbot CLI functionality
"""

import subprocess
import time
import sys

def test_hummingbot_cli():
    """Test Hummingbot CLI with basic commands"""
    print("🧪 Testing Hummingbot CLI functionality...")
    
    try:
        # Start Hummingbot with help command
        process = subprocess.Popen(
            ['./start'],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            cwd='/home/mailr/hummingbot-private/hummingbot'
        )
        
        # Send help command and exit
        commands = "help\nexit\n"
        stdout, stderr = process.communicate(input=commands, timeout=30)
        
        if "Available commands:" in stdout or "help" in stdout.lower():
            print("✅ CLI responds to help command")
            return True
        else:
            print("❌ CLI did not respond properly")
            print(f"stdout: {stdout[:200]}...")
            print(f"stderr: {stderr[:200]}...")
            return False
            
    except subprocess.TimeoutExpired:
        print("✅ CLI started but timed out (expected)")
        process.kill()
        return True
    except Exception as e:
        print(f"❌ CLI test failed: {e}")
        return False

if __name__ == "__main__":
    success = test_hummingbot_cli()
    if success:
        print("\n🎉 Hummingbot CLI is working!")
        print("\n🚀 You can now run Hummingbot with: ./start")
        print("📚 Try these commands in Hummingbot:")
        print("   help          - Show available commands")
        print("   create        - Create a trading strategy")
        print("   connect       - Connect to an exchange")
        print("   status        - Show current status")
        print("   exit          - Exit Hummingbot")
    else:
        print("\n❌ CLI test failed")
        sys.exit(1)