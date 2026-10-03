package ai.haos.robotface;

import android.app.Activity;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.graphics.PixelFormat;
import android.net.Uri;
import android.os.Build;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.util.Log;
import android.view.Gravity;
import android.view.KeyEvent;
import android.view.MotionEvent;
import android.view.View;
import android.view.WindowManager;
import android.widget.FrameLayout;
import android.widget.Toast;

/**
 * Activity Imersiva / Kiosk View com suporte à alternância rápida para o Desktop Android (Opção 1).
 *
 * Funcionalidades:
 * 1. Ocupa a tela inteira em modo Immersive Sticky (esconde barras de status e navegação).
 * 2. Ao clicar no botão Home físico ou ao tocar duas vezes no topo, minimiza ou fecha o Kiosk,
 *    liberando a UI para o Launcher padrão do Android.
 * 3. Temporizador de inatividade configurável (ex: 30s) que faz a Face retornar à frente.
 * 4. Alternativamente pode gerenciar o WindowManager Overlay para flutuar sobre qualquer app.
 */
public class FaceKioskActivity extends Activity implements HaosIpcClient.ExpressionListener {

    private static final String TAG = "FaceKioskActivity";
    private static final long INACTIVITY_TIMEOUT_MS = 30_000; // 30 segundos

    private FaceGLView faceSurfaceView;
    private HaosIpcClient ipcClient;
    private HomeKeyWatcher homeKeyWatcher;
    private Handler inactivityHandler;
    private Runnable returnToFaceRunnable;

    private boolean isOverlayMode = false;
    private WindowManager windowManager;
    private View overlayRootView;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        // Manter a tela sempre ligada enquanto a Face estiver ativa + mostrar
        // acima da tela de bloqueio (kiosk: a face precisa aparecer sem unlock)
        getWindow().addFlags(WindowManager.LayoutParams.FLAG_KEEP_SCREEN_ON
                | WindowManager.LayoutParams.FLAG_TURN_SCREEN_ON
                | WindowManager.LayoutParams.FLAG_SHOW_WHEN_LOCKED
                | WindowManager.LayoutParams.FLAG_DISMISS_KEYGUARD);

        setContentView(R.layout.activity_kiosk);
        faceSurfaceView = findViewById(R.id.face_surface_view);
        handleExprIntent(getIntent());

        inactivityHandler = new Handler(Looper.getMainLooper());
        returnToFaceRunnable = new Runnable() {
            @Override
            public void run() {
                bringFaceToFront();
            }
        };

        // Iniciar cliente IPC
        ipcClient = new HaosIpcClient(this);
        ipcClient.start();

        // Monitorar Botão Home físico do ASUS ZenFone 4
        homeKeyWatcher = new HomeKeyWatcher(this);
        homeKeyWatcher.setOnHomePressedListener(new HomeKeyWatcher.OnHomePressedListener() {
            @Override
            public void onHomePressed() {
                Log.i(TAG, "Botão HOME pressionado -> Alternando para Android Launcher de manutenção.");
                minimizeToAndroid();
            }

            @Override
            public void onHomeLongPressed() {
                Log.i(TAG, "Botão HOME longo ou Recentes -> Alternando para Android Launcher.");
                minimizeToAndroid();
            }
        });
        homeKeyWatcher.startWatch();

        // Adicionar duplo toque na tela para abrir Android Desktop de manutenção
        setupGestureEscape();
    }

    private void setupGestureEscape() {
        if (faceSurfaceView != null) {
            faceSurfaceView.setOnTouchListener(new View.OnTouchListener() {
                private long lastTapTime = 0;
                @Override
                public boolean onTouch(View v, MotionEvent event) {
                    if (event.getAction() == MotionEvent.ACTION_UP) {
                        long now = System.currentTimeMillis();
                        if (now - lastTapTime < 400) { // Duplo toque
                            Toast.makeText(FaceKioskActivity.this, "Alternando para Modo Android...", Toast.LENGTH_SHORT).show();
                            minimizeToAndroid();
                            return true;
                        }
                        lastTapTime = now;
                    }
                    return true;
                }
            });
        }
    }

    @Override
    protected void onResume() {
        super.onResume();
        applyImmersiveSticky();
        cancelInactivityTimer();
    }

    @Override
    protected void onPause() {
        super.onPause();
        // Quando a activity sai de foco (ex: usuário no Launcher de manutenção),
        // engatilha o temporizador de retorno automático à Face
        scheduleInactivityReturn();
    }

    @Override
    protected void onNewIntent(Intent intent) {
        super.onNewIntent(intent);
        setIntent(intent);
        handleExprIntent(intent);
    }

    /**
     * Força um estado via: adb shell am start -n ai.haos.robotface/.FaceKioskActivity
     * --es expr <estado>. Usado na verificacao visual (pausa o demo por IPC_GRACE).
     */
    private void handleExprIntent(Intent intent) {
        if (intent == null || faceSurfaceView == null) return;
        String expr = intent.getStringExtra("expr");
        if (expr != null && !expr.isEmpty()) {
            faceSurfaceView.updateState(expr, 0f, 0f, 0f, false);
            Log.i(TAG, "expr forcada via intent: " + expr);
        }
    }

    @Override
    protected void onDestroy() {
        super.onDestroy();
        if (homeKeyWatcher != null) {
            homeKeyWatcher.stopWatch();
        }
        if (ipcClient != null) {
            ipcClient.stop();
        }
        cancelInactivityTimer();
    }

    /**
     * Aplica o modo Immersive Sticky nativo do Android
     */
    private void applyImmersiveSticky() {
        View decorView = getWindow().getDecorView();
        decorView.setSystemUiVisibility(
                View.SYSTEM_UI_FLAG_IMMERSIVE_STICKY
                        | View.SYSTEM_UI_FLAG_LAYOUT_STABLE
                        | View.SYSTEM_UI_FLAG_LAYOUT_HIDE_NAVIGATION
                        | View.SYSTEM_UI_FLAG_LAYOUT_FULLSCREEN
                        | View.SYSTEM_UI_FLAG_HIDE_NAVIGATION
                        | View.SYSTEM_UI_FLAG_FULLSCREEN
        );
    }

    /**
     * Alternância imediata para a tela inicial / Launcher normal do Android
     */
    public void minimizeToAndroid() {
        // Envia a Activity para segundo plano sem destruir
        moveTaskToBack(true);

        // Garante que o Intent do Launcher padrão seja disparado
        Intent homeIntent = new Intent(Intent.ACTION_MAIN);
        homeIntent.addCategory(Intent.CATEGORY_HOME);
        homeIntent.setFlags(Intent.FLAG_ACTIVITY_NEW_TASK);
        try {
            startActivity(homeIntent);
        } catch (Exception ignored) {}

        // Programa retorno automático após inatividade
        scheduleInactivityReturn();
    }

    /**
     * Reabre a Face do Robô trazendo-a para o primeiro plano total
     */
    public void bringFaceToFront() {
        Log.i(TAG, "Retornando à Face do Robô...");
        Intent intent = new Intent(this, FaceKioskActivity.class);
        intent.addFlags(Intent.FLAG_ACTIVITY_NEW_TASK | Intent.FLAG_ACTIVITY_REORDER_TO_FRONT | Intent.FLAG_ACTIVITY_SINGLE_TOP);
        startActivity(intent);
    }

    private void scheduleInactivityReturn() {
        cancelInactivityTimer();
        inactivityHandler.postDelayed(returnToFaceRunnable, INACTIVITY_TIMEOUT_MS);
    }

    private void cancelInactivityTimer() {
        if (inactivityHandler != null && returnToFaceRunnable != null) {
            inactivityHandler.removeCallbacks(returnToFaceRunnable);
        }
    }

    // Interceptação de teclas de hardware
    @Override
    public boolean onKeyDown(int keyCode, KeyEvent event) {
        if (keyCode == KeyEvent.KEYCODE_BACK) {
            // Tecla voltar minimiza para o Android em vez de fechar
            minimizeToAndroid();
            return true;
        }
        return super.onKeyDown(keyCode, event);
    }

    // Callbacks do Cliente IPC HAOS
    @Override
    public void onExpressionReceived(final String expression, final float lookX, final float lookY, final float amplitude, final boolean blink) {
        runOnUiThread(new Runnable() {
            @Override
            public void run() {
                if (faceSurfaceView != null) {
                    faceSurfaceView.updateState(expression, lookX, lookY, amplitude, blink);
                }
            }
        });
    }

    @Override
    public void onFaceBringToFront() {
        runOnUiThread(new Runnable() {
            @Override
            public void run() {
                bringFaceToFront();
            }
        });
    }

    @Override
    public void onFaceMinimize() {
        runOnUiThread(new Runnable() {
            @Override
            public void run() {
                minimizeToAndroid();
            }
        });
    }
}
