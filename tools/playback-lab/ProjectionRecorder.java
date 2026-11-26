package org.cleanyfin.fixture;

import android.app.*;
import android.content.Intent;
import android.content.pm.ServiceInfo;
import android.media.*;
import android.media.projection.*;
import android.os.*;
import java.io.*;
import java.nio.charset.StandardCharsets;

/** Bounded PCM only, in MODE_PRIVATE files. No microphone source or virtual display. */
public final class ProjectionRecorder extends Service {
    private volatile boolean done;
    private void status(String s) throws IOException {
        try (FileOutputStream f=openFileOutput("status.new",MODE_PRIVATE)) { f.write(s.getBytes(StandardCharsets.US_ASCII)); }
        if (!new File(getFilesDir(),"status.new").renameTo(new File(getFilesDir(),"status"))) throw new IOException();
    }
    @Override public IBinder onBind(Intent i) { return null; }
    @Override public int onStartCommand(Intent i,int flags,int id) {
        if (i==null || "finish".equals(i.getAction())) { done=true; return START_NOT_STICKY; }
        NotificationManager nm=getSystemService(NotificationManager.class);
        nm.createNotificationChannel(new NotificationChannel("control","Synthetic audio control",NotificationManager.IMPORTANCE_LOW));
        startForeground(1,new Notification.Builder(this,"control").setSmallIcon(android.R.drawable.ic_media_play)
            .setContentTitle("Recording synthetic Wholphin playback").build(),ServiceInfo.FOREGROUND_SERVICE_TYPE_MEDIA_PROJECTION);
        new Thread(()->capture(i.getParcelableExtra("grant",Intent.class))).start();
        return START_NOT_STICKY;
    }
    private void capture(Intent grant) {
        MediaProjection projection=null; AudioRecord record=null; String result="failed";
        try {
            projection=getSystemService(MediaProjectionManager.class).getMediaProjection(Activity.RESULT_OK,grant);
            projection.registerCallback(new MediaProjection.Callback(){ @Override public void onStop(){ done=true; } },new Handler(getMainLooper()));
            int uid=getPackageManager().getApplicationInfo("com.github.damontecres.wholphin",0).uid;
            AudioPlaybackCaptureConfiguration config=new AudioPlaybackCaptureConfiguration.Builder(projection)
                .addMatchingUid(uid).addMatchingUsage(AudioAttributes.USAGE_MEDIA).build();
            int mask=AudioFormat.CHANNEL_IN_STEREO, enc=AudioFormat.ENCODING_PCM_16BIT;
            record=new AudioRecord.Builder().setAudioPlaybackCaptureConfig(config).setAudioFormat(new AudioFormat.Builder()
                .setSampleRate(48000).setChannelMask(mask).setEncoding(enc).build())
                .setBufferSizeInBytes(Math.max(32768,4*AudioRecord.getMinBufferSize(48000,mask,enc))).build();
            String format=uid+" "+record.getSampleRate()+" "+record.getChannelCount()+" "+record.getAudioFormat();
            int total=0; byte[] data=new byte[8192]; long end=SystemClock.elapsedRealtime()+90000;
            try (FileOutputStream f=openFileOutput("control.pcm",MODE_PRIVATE)) {
                record.startRecording(); if(record.getRecordingState()!=AudioRecord.RECORDSTATE_RECORDING) throw new IOException();
                status("ready "+format);
                while(!done && SystemClock.elapsedRealtime()<end && total<192000*92) {
                    int n=record.read(data,0,data.length,AudioRecord.READ_NON_BLOCKING);
                    if(n<0) throw new IOException(); if(n==0){SystemClock.sleep(5);continue;} f.write(data,0,n);total+=n;
                }
            }
            result=(done?"closed ":"expired ")+format+" "+total;
        } catch(Exception e) {result="failed "+e.getClass().getSimpleName();}
        finally {
            try { if(record!=null) record.release(); } finally { if(projection!=null) projection.stop(); }
            try {status(result);} catch(IOException ignored) {} stopForeground(STOP_FOREGROUND_REMOVE); stopSelf();
        }
    }
    @Override public void onDestroy(){done=true;super.onDestroy();}
}
