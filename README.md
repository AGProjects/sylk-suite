
# Sylk Suite installer

 - By AG Projects http://ag-projects.com
 - Project homepage https://github.com/AGProjects/sylk-suite

```
curl -L https://sylkserver.com/suite -o sylk.py; sudo python3 sylk.py
```

Use this installer to self-host a SIP/WEBRTC server under your own domain.

 - Zero configuration with DNS zone and end users account enrollment
 - Rich media exchange including audio, video, text chat, file transfers
 - OpenPGP encryption and offline storage for messages and files
 - Multi-party conferencing for all media
 - Data synchronization between multiple devices
 - All open source, no backdoors


# Requirements

 - Debian Bookworm OS
 - Publicly reachable server or behind NAT using port forwarding
 

# Ingredients

 - OpenSIPS https://opensips.org
 - OpenXCAP https://openxcap.org
 - SylkServer https://sylkserver.com
 - Janus https://janus.conf.meetecho.com
 - MediaProxy http://mediaproxy.ag-projects.com
 - MSRP Relay https://msrprelay.org
 - SIP Thor Managed DNS https://mdns.sipthor.net
 - Mobile Android app (Sylk Mobile) https://play.google.com/store/apps/details?id=com.agprojects.sylk
 - Mobile iOS app (Sylk Mobile) https://apps.apple.com/us/app/id1489960733


# Uninstall

Running the install script with --show-installed will display all installed
software. Use the purge command to remove the software.
