---
id: vpn-setup
title: Corporate VPN setup
audience: [employee]
teams: [all]
---
# Corporate VPN setup

## Installing the client
The corporate VPN client is called Secure Connect. Download it from the Software Portal (portal.opspilot.example) under Network > Secure Connect. It supports Windows 10/11, macOS 13 or newer and Ubuntu 22.04. Mobile devices must use the managed Secure Connect app from the company portal.

## Connecting
Open Secure Connect and enter the gateway vpn.opspilot.example. Sign in with your work account using single sign-on. You will be asked for multi-factor authentication through Microsoft Authenticator. After a successful sign-in the client shows "Connected" in the system tray.

## Session rules
Sessions disconnect after 8 hours or after 30 minutes of inactivity. The VPN uses split tunnelling: only traffic for internal systems goes through the gateway, general internet traffic does not. VPN access is enabled by default for every active employee account; contractors need a manager to request it.
