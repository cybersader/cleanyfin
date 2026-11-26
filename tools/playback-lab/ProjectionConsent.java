package org.cleanyfin.fixture;

import android.app.Activity;
import android.content.Intent;
import android.media.projection.MediaProjectionConfig;
import android.media.projection.MediaProjectionManager;
import android.os.Bundle;

/** Only compiled into the disposable emulator's playback-capture build. No display is captured. */
public final class ProjectionConsent extends Activity {
    @Override public void onCreate(Bundle state) {
        super.onCreate(state);
        if (state != null) return;
        if ("finish".equals(getIntent().getAction())) {
            startService(new Intent(this, ProjectionRecorder.class).setAction("finish"));
            finish(); return;
        }
        startActivityForResult(getSystemService(MediaProjectionManager.class).createScreenCaptureIntent(
            MediaProjectionConfig.createConfigForDefaultDisplay()), 1);
    }
    @Override protected void onActivityResult(int request, int result, Intent grant) {
        super.onActivityResult(request, result, grant);
        if (request==1 && result==RESULT_OK && grant!=null) {
            startForegroundService(new Intent(this, ProjectionRecorder.class).putExtra("grant",grant).putExtra("stereo", "stereo".equals(getIntent().getAction())));
        }
        finish();
    }
}
