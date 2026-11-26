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
            // The public SDK has no positional CHANNEL_IN_5POINT1. Ask for the six
            // source lanes by index, then try stereo float and finally stereo s16.
            // A successful builder is not enough: start and read actual frames.
            StringBuilder attempts=new StringBuilder(); int channels=0,encoding=0;
            for(int[] mode:new int[][]{{6,4},{2,4},{2,2}}) {
                try {
                    AudioFormat.Builder f=new AudioFormat.Builder().setSampleRate(48000).setEncoding(mode[1]);
                    if(mode[0]==6) f.setChannelIndexMask(0x3f); else f.setChannelMask(AudioFormat.CHANNEL_IN_STEREO);
                    record=new AudioRecord.Builder().setAudioPlaybackCaptureConfig(config).setAudioFormat(f.build())
                        .setBufferSizeInBytes(98304).build();
                    record.startRecording();
                    if(record.getRecordingState()!=AudioRecord.RECORDSTATE_RECORDING || record.getSampleRate()!=48000 ||
                       record.getChannelCount()!=mode[0] || record.getAudioFormat()!=mode[1]) throw new IllegalStateException();
                    channels=mode[0];encoding=mode[1];attempts.append(channels+"x"+encoding+"-started");break;
                } catch(IllegalArgumentException|IllegalStateException|UnsupportedOperationException e) {
                    attempts.append(mode[0]+"x"+mode[1]+"-"+e.getClass().getSimpleName()+",");
                    if(record!=null){record.release();record=null;}
                }
            }
            if(channels==0)throw new IllegalStateException();
            String format=uid+" "+record.getSampleRate()+" "+channels+" "+encoding+" "+attempts+" "+record.getFormat().getChannelIndexMask()+" "+record.getFormat().getChannelMask();
            int total=0,limit=48000*channels*(encoding==4?4:2)*90;
            java.nio.ByteBuffer direct=java.nio.ByteBuffer.allocateDirect(24576);byte[] data=new byte[direct.capacity()];
            long end=SystemClock.elapsedRealtime()+90000;
            try (FileOutputStream f=openFileOutput("control.pcm",MODE_PRIVATE)) {
                status("ready "+format);
                while(!done && SystemClock.elapsedRealtime()<end && total<limit) {
                    direct.clear();int n=record.read(direct,direct.capacity(),AudioRecord.READ_NON_BLOCKING);
                    if(n<0)throw new IOException();if(n==0){SystemClock.sleep(5);continue;}
                    direct.position(0);direct.get(data,0,n);f.write(data,0,n);total+=n;
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
