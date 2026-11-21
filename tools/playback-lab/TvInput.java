package org.cleanyfin.fixture;

import android.app.Instrumentation;
import android.os.Bundle;
import android.util.Base64;
import android.view.accessibility.AccessibilityNodeInfo;
import java.nio.charset.StandardCharsets;
import java.util.ArrayList;

/** Self-instrument this empty test package, never the Wholphin release. */
public final class TvInput extends Instrumentation {
    private static final String APP="com.github.damontecres.wholphin";
    private Bundle args;
    @Override public void onCreate(Bundle b){super.onCreate(b);args=b;start();}
    private void collect(AccessibilityNodeInfo n, ArrayList<AccessibilityNodeInfo> nodes,int depth){
        if(n==null||depth>48||nodes.size()>2000||!APP.contentEquals(String.valueOf(n.getPackageName())))return;
        nodes.add(n);for(int i=0;i<n.getChildCount();i++)collect(n.getChild(i),nodes,depth+1);
    }
    @Override public void onStart(){
        boolean ok=false;
        try {
            ArrayList<AccessibilityNodeInfo> nodes=new ArrayList<>();collect(getUiAutomation().getRootInActiveWindow(),nodes,0);
            String value=new String(Base64.decode(args.getString("value"),Base64.DEFAULT),StandardCharsets.UTF_8);
            boolean title=false,field=false;AccessibilityNodeInfo save=null;
            for(AccessibilityNodeInfo n:nodes){
                if(!n.isVisibleToUser())continue;
                String text=String.valueOf(n.getText());
                title|="Edit mpv.conf".equals(text);field|=n.isEditable()&&value.equals(text);
                if("Save".equals(text))save=n;
            }
            if("save".equals(args.getString("op"))&&title&&field){
                for(AccessibilityNodeInfo n=save;n!=null&&APP.contentEquals(String.valueOf(n.getPackageName()));n=n.getParent()){
                    if(n.isClickable()&&n.isEnabled()&&n.performAction(AccessibilityNodeInfo.ACTION_CLICK)){ok=true;break;}
                }
            }
        }catch(RuntimeException unavailable){/* Caller must re-open the editor and verify the exact contents. */}
        Bundle result=new Bundle();result.putString("result",ok?"applied":"not-found");finish(0,result);
    }
}
