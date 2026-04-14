// to-json.js
const fs = require('fs');
const config = require('./src/app/config.js');
const { defaultDomain } = require('./src/app/config.js');

config.testNumbers = [
    {
      "uri": `echo@${defaultDomain}`,
      "name": "Test microphone"
    },
    {
      "uri": `playback@${defaultDomain}`,
      "name": "Test video"
    }
  ];

fs.writeFileSync('./sylk-config.json', JSON.stringify(config, null, 2));

console.log('sylk-config.json created');
