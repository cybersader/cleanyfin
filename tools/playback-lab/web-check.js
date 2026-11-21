import {video, event, load, seekSource} from './web-player.js';

const results = document.querySelector('#results');
const controls = Array.from(document.querySelectorAll('#test,#original,#filtered'));
async function action(fn) {
    controls.forEach(b => b.disabled = true);
    try { await fn(); } catch (error) { results.textContent += `\nFAIL: ${error.message}`; video.pause(); }
    finally { controls.forEach(b => b.disabled = false); }
}
document.querySelector('#original').onclick = () => action(() => load(false));
document.querySelector('#filtered').onclick = () => action(() => load(true));

// Sample presented frames and the dominant fixture tone. Positive controls are essential:
// blanking the whole movie or losing audio capture is not a successful cut.
async function sample(filtered, seekBack) {
    await load(filtered);
    let landed = null;
    if (seekBack) {
        video.pause(); const sought = event(video, 'seeked'); seekSource(4); await sought;
        landed = video.currentTime; await video.play();
    }
    const canvas = document.createElement('canvas'); canvas.width = canvas.height = 1;
    const ctx = canvas.getContext('2d', {willReadFrequently:true});
    const tracks = video.captureStream().getAudioTracks();
    if (!tracks.length) throw Error('Cannot measure browser audio');
    const audio = new AudioContext();
    let callback;
    try {
        const node = audio.createMediaStreamSource(new MediaStream(tracks));
        const analyser = audio.createAnalyser(); analyser.fftSize = 4096; analyser.smoothingTimeConstant = 0;
        node.connect(analyser); await audio.resume();
        const bins = new Float32Array(analyser.frequencyBinCount);
        const counts = {blue:0,red:0,green:0,440:0,660:0,880:0};
        function frame() {
            ctx.drawImage(video,0,0,1,1);
            const [r,g,b] = ctx.getImageData(0,0,1,1).data;
            if(r>150 && g<80 && b<80) counts.red++;
            if(g>150 && r<80 && b<80) counts.green++;
            if(b>150 && r<80 && g<80) counts.blue++;
            analyser.getFloatFrequencyData(bins);
            const levels = [440,660,880].map(hz=>[hz,bins[Math.round(hz*4096/audio.sampleRate)]]);
            const [tone,level] = levels.sort((a,b)=>b[1]-a[1])[0];
            if(level > -55) counts[tone]++;
            callback = video.requestVideoFrameCallback(frame);
        }
        callback = video.requestVideoFrameCallback(frame);
        await event(video,'ended',16000);
        const duration=video.duration;
        const passed = counts.green>0 && counts[660]>0 && (seekBack || (counts.blue>0 && counts[440]>0)) &&
            (filtered ? counts.red===0 && counts[880]===0 && duration<6.45 && duration>6.39 : counts.red>0 && counts[880]>0 && duration>9.59) &&
            (!seekBack || (landed>=3.2 && landed<3.24));
        return {passed, duration, landed, counts};
    } finally { if(callback !== undefined) video.cancelVideoFrameCallback(callback); await audio.close(); }
}
document.querySelector('#test').onclick = () => action(async()=>{
    results.textContent = '';
    for(const [name, filtered, seek] of [['original',false,false],['filtered',true,false],['seek into removed scene + resume',true,true]]) {
        const result=await sample(filtered,seek);
        results.textContent += `${result.passed?'PASS':'FAIL'} ${name}: ${JSON.stringify(result)}\n`;
        if(!result.passed) throw Error(`${name} failed`);
    }
});
