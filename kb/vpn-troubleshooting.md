---
id: vpn-troubleshooting
title: VPN troubleshooting and profile reset
audience: [employee]
teams: [all]
---
# VPN troubleshooting

## Error "Gateway unreachable" or error 809
This almost always means the network blocks the VPN ports. Secure Connect needs UDP 4500 or TCP 443 outbound. Hotel, airport and some home routers block UDP 4500. Switch to the TCP 443 fallback in Settings > Protocol, or connect through a phone hotspot.

## Sign-in loops or "Authentication failed"
Check that the system clock is correct, then sign out of the client, clear saved credentials in Settings > Account and sign in again. If MFA prompts never arrive, see the MFA enrolment guide.

## Resetting the VPN profile
If the client still fails, the VPN profile may be corrupted. Request a VPN profile reset: IT removes your device certificate and re-issues it. A VPN reset is an approval-gated action, so an IT administrator must approve it before it runs. Approval normally takes under 2 business hours during support hours. After approval, restart the client to pick up the new profile.

## When to open a ticket
Open a ticket with category vpn if the problem continues after a profile reset, or if several colleagues on the same network are affected.
