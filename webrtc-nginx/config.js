'use strict';

const defaultDomain = 'sylk.link';
const port = process.env.WEB_PORT && process.env.WEB_PORT !== '443'
  ? `:${process.env.WEB_PORT}`
  : '';
  
const configOptions = {
    defaultDomain           : defaultDomain,
    enrollmentDomain        : defaultDomain,
    nonSipDomains           : [],           // Each domain configured here will be used for alternate authentication methods
    publicUrl               : 'https://webrtc.sipthor.net',
    enrollmentUrl           : 'https://blink.sipthor.net/enrollment-sylk-mobile.phtml',
    defaultConferenceDomain : 'videoconference.sip2sip.info',
    defaultGuestDomain      : `guest.${defaultDomain}`,
    wsServer                : `wss://${defaultDomain}${port}/ws`,
    fileSharingUrl          : `https://${defaultDomain}${port}/filesharing`,
    fileTransferUrl         : `https://${defaultDomain}${port}/filetransfer`,
    iceServers              : [{urls: 'stun:stun.sipthor.net:3478'}],
    muteGuestAudioOnJoin    : false,
    guestUserPermissions    : {
        allowMuteAllParticipants     : false,
        allowToggleHandsParticipants : false
    },
    showGuestCompleteScreen : true,
    downloadUrl             : 'https://sylkserver.com'
};


module.exports = configOptions;
