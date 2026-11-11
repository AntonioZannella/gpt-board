#!/usr/bin/env python3
"""Renew the existing native app without deleting its notebook recovery storage."""
import argparse
import subprocess
from pathlib import Path


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--device', help='Connected device identifier from devicectl')
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    if not args.device:
        subprocess.run(['xcrun', 'devicectl', 'list', 'devices'], check=True)
        print('Wait for Saved in GPT Board, then rerun with --device IDENTIFIER.')
        return
    build = root / 'work/ipad-build'
    subprocess.run(['xcodebuild', '-project', str(root / 'ios/GPTBoardPad.xcodeproj'),
                    '-scheme', 'GPTBoardPad', '-configuration', 'Debug', '-sdk', 'iphoneos',
                    '-destination', 'generic/platform=iOS', '-derivedDataPath', str(build), 'build'], check=True)
    subprocess.run(['xcrun','devicectl','device','install','app','--device',args.device,
                    str(build / 'Build/Products/Debug-iphoneos/GPTBoardPad.app')],check=True)
    subprocess.run(['xcrun','devicectl','device','process','launch','--device',args.device,
                    'com.antoniozannella.gptboard'],check=True)
if __name__ == '__main__': main()
