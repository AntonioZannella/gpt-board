# GPT Board for iPad

A small native WKWebView app that uses the existing GPT Board server. No new notebook store or drawing engine. Requires iPadOS 17 or newer. Pencil double-tap requires a compatible Apple Pencil (2nd generation or Pro).

## Build and install

1. Open `GPTBoardPad.xcodeproj` in Xcode.
2. Select the GPTBoardPad target → Signing & Capabilities. Copy `Signing.local.xcconfig.example` to `Signing.local.xcconfig` and set your team identifier, or select your Apple development team in Xcode. The local configuration is ignored by Git. Use your personal Apple account in Xcode if needed; do not put account credentials in source files.
3. Connect and unlock the iPad, trust the Mac when asked, and enable Developer Mode if iPadOS requests it.
4. Select the iPad as the run destination and press Run. Personal-team installs may need periodic rebuilding/signing; Xcode shows the applicable provisioning details.
5. Keep GPT Board running on the Mac. Allow Local Network access when iPadOS asks.
6. If the connection prompt appears, enter the URL from the Mac's **Connect iPad** dialog. Use the actual address shown by your Mac; `gpt-board.local` is only a placeholder until you connect. The numeric URL is a fallback.
7. Pair using the current code or paste the one-time pairing link. Pairing is separate from Safari; the app retains its own cookie and IndexedDB recovery storage. Wait for Saved in Safari before switching apps or server addresses.
8. Set iPad Settings → Apple Pencil → Double Tap to switch between current tool and eraser. Open a notebook and double-tap the side of the Pencil. The app respects the system's disabled/ignore setting; enabled double-tap toggles GPT Board pen and eraser.

There is no native toolbar. Pencil double-tap uses the board's existing tool buttons. Files → Share → GPT Board opens an import preview after the board loads; the Files menu also supports the normal file picker. PNG, PDF and recovery exports use the iPad share sheet. Failed initial connections retry while the app is active, without reloading an already-open board or its unsaved writing. The app opens external web links in Safari and keeps its main view on the selected board origin. It does not bypass TLS errors or install certificates.

## Checks

`Tests/ConnectionTests.swift` runs with `BoardConnection.swift` on macOS using swiftc. `tests/pencil-toggle.test.mjs` in the repository tests the bundled JavaScript tool switch.

A generic-device build can be checked with `xcodebuild -project ios/GPTBoardPad.xcodeproj -scheme GPTBoardPad -destination 'generic/platform=iOS' CODE_SIGNING_ALLOWED=NO build` once Xcode's iOS platform support is installed. An unsigned build cannot be installed on an iPad.

Physical acceptance: pair, draw, double-tap to erase, double-tap back to pen, confirm Mac sync/follow, background/reopen, export a PNG, and retry after Wi-Fi loss. A simulator cannot verify physical Pencil double-tap or palm behavior.

## Renew or update the installed app

With the iPad connected and unlocked, run `python3 scripts/update_ipad.py --device DEVICE_ID`. It builds, installs over the existing bundle, and launches the app without deleting its data. Omit `--device` to list devices. Personal Team signing still expires after seven days; this helper does not bypass that rule. Wait for Saved before updating.
