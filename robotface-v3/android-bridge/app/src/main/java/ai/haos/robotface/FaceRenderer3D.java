package ai.haos.robotface;

import android.opengl.GLES30;
import android.opengl.GLSurfaceView;
import android.opengl.Matrix;
import android.os.SystemClock;
import android.util.Log;

import java.nio.ByteBuffer;
import java.nio.ByteOrder;
import java.nio.FloatBuffer;

import javax.microedition.khronos.egl.EGLConfig;
import javax.microedition.khronos.opengles.GL10;

/**
 * Renderer OpenGL ES 3.0 da Face Robótica 3D — versão "estúdio" anti-serrilhado.
 *
 * Estilo alvo (sprite sheet de referência do dono, 26 expressões):
 *  robô chibi EVE/WALL-E — capacete/casca branca perolada arredondada,
 *  visor facial escuro convexo (vidro curvo) e expressões luminosas
 *  verde-menta/ciano neon projetadas na curvatura do visor; rosa/magenta no
 *  estado CARINHOSO. Sem serrilhado.
 *
 * Pipeline de qualidade (tudo GLSL ES 3.0 / #version 300 es):
 *  1. CENA  -> FBO multisample (MSAA 8x/4x, medido via GL_MAX_SAMPLES) com
 *     renderbuffers color+depth multisampled;
 *  2. RESOLVE -> glBlitFramebuffer para FBO de textura RGBA8;
 *  3. BLOOM  -> bright-pass por SATURAÇÃO+luminância (máscara dos traços
 *     neon, exclui a casca branca) com downsample 1/4 + 4 passes de blur
 *     gaussiano 9-taps (H,V,H,V) em FBOs próprios;
 *  4. COMPOSITE -> FXAA (anti-serrilhado de pós-processamento, cobre
 *     primitivas finas que o MSAA não resolve bem) + bloom aditivo +
 *     vinheta sutil + tone mapping simples, desenhado no framebuffer da janela.
 *
 * Se o MSAA não estiver disponível/incompleto no hardware, o pipeline cai
 * deterministicamente para FXAA (passo 4 sempre ativo).
 *
 * Ritmo: alvo de 30 FPS (limitador de frame em 33,33 ms) — prioridade de
 * qualidade sobre framerate, conforme pedido do dono.
 *
 * Malha 100% procedural (sem assets externos), iluminação Phong + hemisférica
 * + rim, animação contínua (respiração, rotação de cabeça, piscar, gaze) e
 * ciclo de demonstração de 11 estados expressivos quando não há IPC.
 */
public class FaceRenderer3D implements GLSurfaceView.Renderer {

    private static final String TAG = "Face3D";

    // Estados canônicos (26 legendas do sprite sheet) percorridos sem IPC
    private static final String[] DEMO_STATES = {
            "padrao", "feliz", "alegre", "piscando", "saudacao", "curioso",
            "pensativo", "falando", "escutando", "focado", "surpreso",
            "empolgado", "confuso", "cetico", "triste", "preocupado",
            "irritado", "confiante", "relaxado", "sonolento", "carinhoso",
            "saudacao_completa", "hud", "processando", "comemoracao",
            "escuta_ativa"
    };
    private static final long DEMO_STEP_MS = 4000L;
    private static final long IPC_GRACE_MS = 7000L;

    /** Ritmo alvo do dono: qualidade primeiro, 30 FPS estáveis. */
    private static final long TARGET_FRAME_NS = 33_333_333L;
    /** Margem para o swap cair no tick de vsync (60 Hz) após o limitador. */
    private static final long VSYNC_MARGIN_NS = 3_000_000L;

    // ==================================================== shaders da cena
    private static final String VERT_SRC =
            "#version 300 es\n"
            + "layout(location=0) in vec3 aPos;\n"
            + "layout(location=1) in vec3 aNrm;\n"
            + "layout(location=2) in vec4 aCol;\n"
            + "uniform mat4 uMVP;\n"
            + "uniform mat4 uModel;\n"
            + "out vec3 vN;\n"
            + "out vec3 vW;\n"
            + "out vec4 vC;\n"
            + "void main() {\n"
            + "  vec4 w = uModel * vec4(aPos, 1.0);\n"
            + "  vW = w.xyz;\n"
            + "  vN = mat3(uModel) * aNrm;\n"
            + "  vC = aCol;\n"
            + "  gl_Position = uMVP * vec4(aPos, 1.0);\n"
            + "}\n";

    private static final String FRAG_SRC =
            "#version 300 es\n"
            + "precision highp float;\n"
            + "in vec3 vN;\n"
            + "in vec3 vW;\n"
            + "in vec4 vC;\n"
            + "uniform vec3 uCam;\n"
            + "uniform vec3 uTint;\n"
            + "uniform float uAlpha;\n"
            + "uniform int uMode;\n"
            + "out vec4 o;\n"
            + "void main() {\n"
            + "  if (uMode == 1) {                 // emissivo (traços neon): cor pura\n"
            + "    o = vec4(vC.rgb * uTint * uAlpha, 1.0);\n"
            + "    return;\n"
            + "  }\n"
            + "  vec3 N = normalize(vN);\n"
            + "  vec3 V = normalize(uCam - vW);\n"
            + "  vec3 L = normalize(vec3(-0.45, 0.62, 0.70));\n"   // key light: sup-esq-frente
            + "  float d = max(dot(N, L), 0.0);\n"
            + "  vec3 H = normalize(L + V);\n"
            + "  float s = pow(max(dot(N, H), 0.0), 64.0);\n"
            + "  float hemi = 0.5 + 0.5 * N.y;\n"
            + "  vec3 amb = mix(vec3(0.13, 0.14, 0.17), vec3(0.34, 0.35, 0.38), hemi);\n"
            + "  float rim = pow(1.0 - max(dot(N, V), 0.0), 3.0);\n"
            + "  vec3 base = vC.rgb * uTint;\n"
            + "  // gradiente suave no visor/vida do material (mais claro no topo)\n"
            + "  float grad = clamp(vW.y * 0.35 + 0.5, 0.0, 1.0);\n"
            + "  base *= mix(0.86, 1.10, grad);\n"
            + "  vec3 col = base * (amb + d * vec3(1.0, 0.99, 0.95) * 0.92)\n"
            + "           + vec3(1.0) * s * 0.42\n"
            + "           + vec3(0.45, 0.75, 1.0) * rim * 0.30;\n"
            + "  o = vec4(col, 1.0);\n"
            + "}\n";

    // ==================================================== shaders de pós
    private static final String POST_VERT =
            "#version 300 es\n"
            + "layout(location=0) in vec2 aPos;\n"
            + "out vec2 vUV;\n"
            + "void main() {\n"
            + "  vUV = aPos * 0.5 + 0.5;\n"
            + "  gl_Position = vec4(aPos, 0.0, 1.0);\n"
            + "}\n";

    /** Bright-pass: máscara por saturação+luminância (só os traços neon bloomam). */
    private static final String BRIGHT_FRAG =
            "#version 300 es\n"
            + "precision highp float;\n"
            + "in vec2 vUV;\n"
            + "out vec4 o;\n"
            + "uniform sampler2D uScene;\n"
            + "uniform vec3 uThr;   // x=satLo, y=satHi, z=lumLo\n"
            + "void main() {\n"
            + "  vec3 c = texture(uScene, vUV).rgb;\n"
            + "  float mx = max(c.r, max(c.g, c.b));\n"
            + "  float mn = min(c.r, min(c.g, c.b));\n"
            + "  float sat = mx - mn;\n"
            + "  float lum = dot(c, vec3(0.2126, 0.7152, 0.0722));\n"
            + "  float m = smoothstep(uThr.x, uThr.y, sat) * smoothstep(uThr.z, uThr.z + 0.35, lum);\n"
            + "  o = vec4(c * m, 1.0);\n"
            + "}\n";

    /** Gaussiana 9-taps com amostragem linear (2 pesos por lado). */
    private static final String BLUR_FRAG =
            "#version 300 es\n"
            + "precision highp float;\n"
            + "in vec2 vUV;\n"
            + "out vec4 o;\n"
            + "uniform sampler2D uTex;\n"
            + "uniform vec2 uDir;\n"
            + "void main() {\n"
            + "  vec3 s = texture(uTex, vUV).rgb * 0.2270270270;\n"
            + "  s += texture(uTex, vUV + uDir * 1.3846153846).rgb * 0.3162162162;\n"
            + "  s += texture(uTex, vUV - uDir * 1.3846153846).rgb * 0.3162162162;\n"
            + "  s += texture(uTex, vUV + uDir * 3.2307692308).rgb * 0.0702702703;\n"
            + "  s += texture(uTex, vUV - uDir * 3.2307692308).rgb * 0.0702702703;\n"
            + "  o = vec4(s, 1.0);\n"
            + "}\n";

    /** Composite final: FXAA + bloom + vinheta + tone mapping ( framebuffer da janela ). */
    private static final String COMPOSITE_FRAG =
            "#version 300 es\n"
            + "precision highp float;\n"
            + "in vec2 vUV;\n"
            + "out vec4 o;\n"
            + "uniform sampler2D uScene;\n"
            + "uniform sampler2D uBloom;\n"
            + "uniform vec2 uTexel;\n"
            + "uniform vec2 uPost;   // x=bloomAmount, y=vignette\n"
            + "float lum(vec3 c) { return dot(c, vec3(0.299, 0.587, 0.114)); }\n"
            + "vec3 fxaa() {\n"
            + "  vec2 t = uTexel;\n"
            + "  vec3 cM  = texture(uScene, vUV).rgb;\n"
            + "  vec3 cNW = texture(uScene, vUV + vec2(-1.0, -1.0) * t).rgb;\n"
            + "  vec3 cNE = texture(uScene, vUV + vec2( 1.0, -1.0) * t).rgb;\n"
            + "  vec3 cSW = texture(uScene, vUV + vec2(-1.0,  1.0) * t).rgb;\n"
            + "  vec3 cSE = texture(uScene, vUV + vec2( 1.0,  1.0) * t).rgb;\n"
            + "  float lM = lum(cM), lNW = lum(cNW), lNE = lum(cNE), lSW = lum(cSW), lSE = lum(cSE);\n"
            + "  float lMin = min(lM, min(min(lNW, lNE), min(lSW, lSE)));\n"
            + "  float lMax = max(lM, max(max(lNW, lNE), max(lSW, lSE)));\n"
            + "  if (lMax - lMin < max(0.0312, lMax * 0.125)) return cM;\n"   // sem borda
            + "  vec2 dir = vec2(-((lNW + lNE) - (lSW + lSE)), ((lNW + lSW) - (lNE + lSE)));\n"
            + "  float dirReduce = max((lNW + lNE + lSW + lSE) * 0.03125, 0.0078125);\n"
            + "  float rcpMin = 1.0 / (min(abs(dir.x), abs(dir.y)) + dirReduce);\n"
            + "  dir = clamp(dir * rcpMin, vec2(-8.0), vec2(8.0)) * t;\n"
            + "  vec3 a = 0.5 * (texture(uScene, vUV + dir * (1.0 / 3.0 - 0.5)).rgb\n"
            + "                + texture(uScene, vUV + dir * (2.0 / 3.0 - 0.5)).rgb);\n"
            + "  vec3 b = a * 0.5 + 0.25 * (texture(uScene, vUV - dir * 0.5).rgb\n"
            + "                            + texture(uScene, vUV + dir * 0.5).rgb);\n"
            + "  float lB = lum(b);\n"
            + "  return (lB < lMin || lB > lMax) ? a : b;\n"
            + "}\n"
            + "void main() {\n"
            + "  vec3 col = fxaa();\n"
            + "  col += texture(uBloom, vUV).rgb * uPost.x;\n"
            + "  vec2 q = vUV - 0.5;\n"
            + "  col *= clamp(1.0 - uPost.y * dot(q, q) * 1.7, 0.0, 1.0);\n"   // vinheta
            + "  col = col / (1.0 + max(col - vec3(0.85), vec3(0.0)));\n"       // tone map simples
            + "  o = vec4(col, 1.0);\n"
            + "}\n";

    // ============================================================ geometria
    /** Malha intercalada: posições (3), normais (3), cores (4) — 3 VBOs. */
    private static class Mesh {
        float[] pos, nrm, col;
        int count;
        final int[] vbo = new int[3];

        Mesh(float[] pos, float[] nrm, float[] col) {
            this.pos = pos;
            this.nrm = nrm;
            this.col = col;
            this.count = pos.length / 3;
        }
    }

    // Escala do modelo e câmera (tela retrato 1080x1920)
    private static final float MODEL_SCALE = 1.08f;
    private static final float CAM_Z = 6.2f;
    private static final float CAM_Y = 0.05f;
    private static final float TARGET_Y = -0.15f;
    private static final float FOV_Y = 45.0f;

    // Dimensões da cabeça (elipsoide) e da máscara/visor frontal
    private static final float RX = 1.0f, RY = 1.25f, RZ = 0.95f;
    private static final float MASK = 1.04f;
    private static final float EYE_X = 0.44f, EYE_Y = 0.16f, EYE_R = 0.30f;
    private static final float BROW_Y = 0.57f;
    private static final float MOUTH_Y = -0.45f;
    private static final float MOUTH_HALF = 0.34f;
    private static final int MOUTH_SEGS = 9;

    private final FaceGLView view;

    // Programs
    private int program;
    private int uMVP, uModel, uCam, uTint, uAlpha, uMode;
    private int progBright, progBlur, progComposite;
    private int uBrightScene, uBrightThr;
    private int uBlurTex, uBlurDir;
    private int uCompScene, uCompBloom, uCompTexel, uCompPost;

    // Malhas
    private Mesh headMesh, neckMesh, earMesh, earGlowMesh, socketMesh;
    private Mesh ringOuter, ringMid, ringCore, pupilMesh, haloMesh;
    private Mesh browBox, mouthBox, auraMesh, heartMesh;
    // v3: busto de corpo + extras do sprite sheet
    private Mesh torsoMesh, upperArmMesh, foreArmMesh, handMesh;
    private Mesh starMesh, zMesh, panelMesh, barMesh, questionMesh;
    private Mesh bulbMesh, ledRingMesh;

    // Matrizes
    private final float[] proj = new float[16];
    private final float[] viewM = new float[16];
    private final float[] pv = new float[16];
    private final float[] mvp = new float[16];
    private final float[] tmp = new float[16];
    private final float[] tmp2 = new float[16];
    private final float[] mHead = new float[16];
    private final float[] mBody = new float[16];
    private final float[] mArm = new float[16];
    private final float[] mFore = new float[16];
    private final float[] mHand = new float[16];
    private final float[] mNeck = new float[16];
    private final float[] mAura = new float[16];
    private final float[] mEye = new float[16];
    private final float[] mPart = new float[16];
    private final float[] camPos = new float[]{0f, CAM_Y, CAM_Z};

    // ---- FBOs / pós-processamento
    private int width, height, bloomW, bloomH;
    private int msaaSamples = 0;      // 0 = sem MSAA (FXAA puro)
    private int maxSamples = 0;       // GL_MAX_SAMPLES medido
    private int msaaFbo, msaaColorRb, msaaDepthRb;
    private int sceneFbo, sceneTex, sceneDepthRb;
    private int brightFbo, brightTex;
    private int blurFboA, blurTexA;
    private int blurFboB, blurTexB;
    private int sceneVAO, postVAO, postVBO;
    private boolean targetsReady = false;

    // Estado de animação
    private float animT = 0f;
    private float lastFrameMs = 0f;
    private float blinkPhase = 0f;
    private float blinkTimer = 0f;
    private long lastDemoStepMs = 0L;
    private int demoIdx = 0;
    private String currentExpr = "padrao";

    // Valores suavizados
    private final float[] eyeCur = {0.24f, 1.00f, 0.75f};
    private float gazeGX = 0f, gazeGY = 0f;
    private float browTiltL = 0f, browTiltR = 0f, browRaiseL = 0f, browRaiseR = 0f;
    private float mouthK = 0f, mouthAmp = 0f;
    private float headYaw = 0f, headPitch = 0f, headRoll = 0f;
    private float blinkTarget = 1f;
    private float eyeScale = 1f;
    private float heartAmt = 0f;
    private float bodyAmt = 0f;
    private float winkAmt = 0f;
    private float starAmt = 0f;
    private float zzzAmt = 0f;
    private float questAmt = 0f;
    private float holoAmt = 0f;
    private float bulbAmt = 0f;
    private float confettiAmt = 0f;
    private float waveAmt = 0f;
    // pose dos braços: lift=rotação-Z (p/ fora), fwd=rotação-X (p/ frente), elbow=cotovelo
    private float armLL = 0f, armLR = 0f, armFL = 0f, armFR = 0f, armEL = 0f, armER = 0f;

    // FPS / telemetria
    private long fpsWindowStart = 0;
    private int fpsFrames = 0;
    private long renderNsAccum = 0;
    private boolean glInfoLogged = false;
    private long lastQualityChangeMs = 0L;
    private long frameStartNs = 0L;

    // Alvos por expressão (recomputados a cada frame)
    private final float[] eyeTarget = new float[3];
    private float tYaw, tPitch, tRoll, tBrowTL, tBrowTR, tBrowRL, tBrowRR,
            tMouthK, tBlink, tGazeX, tGazeY, tEyeScale, tHeart;
    private float tBody, tWink, tStar, tZzz, tQuest, tHolo, tBulb, tConfetti, tWave;
    private float tArmLL, tArmLR, tArmFL, tArmFR, tArmEL, tArmER;

    public FaceRenderer3D(FaceGLView view) {
        this.view = view;
    }

    // =============================================================== GL lifecycle
    @Override
    public void onSurfaceCreated(GL10 unused, EGLConfig config) {
        frameStartNs = System.nanoTime();
        GLES30.glClearColor(0.012f, 0.014f, 0.020f, 1.0f);
        GLES30.glEnable(GLES30.GL_DEPTH_TEST);
        GLES30.glDepthFunc(GLES30.GL_LESS);
        GLES30.glDisable(GLES30.GL_CULL_FACE); // windings procedurais heterogêneos
        GLES30.glDisable(GLES30.GL_BLEND);

        program = createProgram(VERT_SRC, FRAG_SRC);
        uMVP = GLES30.glGetUniformLocation(program, "uMVP");
        uModel = GLES30.glGetUniformLocation(program, "uModel");
        uCam = GLES30.glGetUniformLocation(program, "uCam");
        uTint = GLES30.glGetUniformLocation(program, "uTint");
        uAlpha = GLES30.glGetUniformLocation(program, "uAlpha");
        uMode = GLES30.glGetUniformLocation(program, "uMode");

        progBright = createProgram(POST_VERT, BRIGHT_FRAG);
        uBrightScene = GLES30.glGetUniformLocation(progBright, "uScene");
        uBrightThr = GLES30.glGetUniformLocation(progBright, "uThr");

        progBlur = createProgram(POST_VERT, BLUR_FRAG);
        uBlurTex = GLES30.glGetUniformLocation(progBlur, "uTex");
        uBlurDir = GLES30.glGetUniformLocation(progBlur, "uDir");

        progComposite = createProgram(POST_VERT, COMPOSITE_FRAG);
        uCompScene = GLES30.glGetUniformLocation(progComposite, "uScene");
        uCompBloom = GLES30.glGetUniformLocation(progComposite, "uBloom");
        uCompTexel = GLES30.glGetUniformLocation(progComposite, "uTexel");
        uCompPost = GLES30.glGetUniformLocation(progComposite, "uPost");

        // Malhas procedurais
        headMesh = buildHead();
        neckMesh = buildNeck();
        earMesh = buildEars();
        earGlowMesh = buildEarGlow();
        socketMesh = buildSockets();
        ringOuter = buildAnnulus(0.70f, 1.00f, 56, 0.40f);
        ringMid = buildAnnulus(0.44f, 0.72f, 56, 1.00f);
        ringCore = buildAnnulus(0.18f, 0.46f, 56, 1.80f);
        pupilMesh = buildDisc(0.17f, 40, 1.0f);
        haloMesh = buildHalo();
        browBox = buildBox();
        mouthBox = buildBox();
        auraMesh = buildDisc(1.0f, 56, 1.0f);
        heartMesh = buildHeart();
        torsoMesh = buildTorso();
        upperArmMesh = buildCapsule(0.145f, 0.54f);
        foreArmMesh = buildCapsule(0.125f, 0.50f);
        handMesh = buildHand();
        starMesh = buildStar(1f, 0.42f);
        zMesh = buildZ();
        panelMesh = buildPanel();
        barMesh = buildBox();
        questionMesh = buildQuestion();
        bulbMesh = buildBulb();
        ledRingMesh = buildAnnulus(0.16f, 0.27f, 40, 1.20f);

        upload(headMesh);
        upload(neckMesh);
        upload(earMesh);
        upload(earGlowMesh);
        upload(socketMesh);
        upload(ringOuter);
        upload(ringMid);
        upload(ringCore);
        upload(pupilMesh);
        upload(haloMesh);
        upload(browBox);
        upload(mouthBox);
        upload(auraMesh);
        upload(heartMesh);
        upload(torsoMesh);
        upload(upperArmMesh);
        upload(foreArmMesh);
        upload(handMesh);
        upload(starMesh);
        upload(zMesh);
        upload(panelMesh);
        upload(barMesh);
        upload(questionMesh);
        upload(bulbMesh);
        upload(ledRingMesh);

        sceneVAO = createVAO();
        GLES30.glBindVertexArray(sceneVAO);

        // VAO/VBO do triângulo full-screen do pós-processamento
        int[] va = new int[1];
        GLES30.glGenVertexArrays(1, va, 0);
        postVAO = va[0];
        int[] vb = new int[1];
        GLES30.glGenBuffers(1, vb, 0);
        postVBO = vb[0];
        float[] tri = {-1f, -1f, 3f, -1f, -1f, 3f};
        FloatBuffer fb = ByteBuffer.allocateDirect(tri.length * 4)
                .order(ByteOrder.nativeOrder()).asFloatBuffer();
        fb.put(tri);
        ((java.nio.Buffer) fb).position(0);
        GLES30.glBindBuffer(GLES30.GL_ARRAY_BUFFER, postVBO);
        GLES30.glBufferData(GLES30.GL_ARRAY_BUFFER, tri.length * 4, fb, GLES30.GL_STATIC_DRAW);

        // ---------------- medição real do hardware (nada assumido)
        int[] v = new int[1];
        GLES30.glGetIntegerv(GLES30.GL_MAX_SAMPLES, v, 0);
        maxSamples = v[0];
        int[] fbSamples = new int[1];
        GLES30.glGetIntegerv(0x80A9 /* GL_SAMPLES (framebuffer padrão) */, fbSamples, 0);
        String exts = GLES30.glGetString(GLES30.GL_EXTENSIONS) == null
                ? "" : GLES30.glGetString(GLES30.GL_EXTENSIONS);
        boolean hasMsaaExt = exts.contains("multisample") || exts.contains("GL_EXT_framebuffer_multisample");

        msaaSamples = maxSamples >= 8 ? 8 : (maxSamples >= 4 ? 4 : 0);

        if (!glInfoLogged) {
            glInfoLogged = true;
            Log.i(TAG, "FX GL_VERSION=" + GLES30.glGetString(GLES30.GL_VERSION)
                    + " GL_RENDERER=" + GLES30.glGetString(GLES30.GL_RENDERER)
                    + " GLSL=" + GLES30.glGetString(GLES30.GL_SHADING_LANGUAGE_VERSION));
            Log.i(TAG, "FX MSAA medido: GL_MAX_SAMPLES=" + maxSamples
                    + " defaultFB_GL_SAMPLES=" + fbSamples[0]
                    + " ext_multisample=" + hasMsaaExt
                    + " -> alvo_MSAA=" + msaaSamples
                    + " (fallback FXAA ativo em qualquer caso)");
            // subconjunto de extensões relevantes (rastro de evidência)
            StringBuilder keep = new StringBuilder();
            for (String s : exts.split(" ")) {
                if (s.contains("multisample") || s.contains("framebuffer")
                        || s.contains("texture_filter") || s.contains("color_buffer")
                        || s.contains("QCOM") || s.contains("discard")) {
                    if (keep.length() < 400) keep.append(s).append(' ');
                }
            }
            Log.i(TAG, "FX exts[rel]=" + keep.toString().trim());
        }
        fpsWindowStart = SystemClock.uptimeMillis();
        fpsFrames = 0;
        renderNsAccum = 0;
        lastFrameMs = SystemClock.uptimeMillis();
    }

    @Override
    public void onSurfaceChanged(GL10 unused, int w, int h) {
        width = w;
        height = h;
        GLES30.glViewport(0, 0, w, h);
        float aspect = h > 0 ? (float) w / (float) h : 0.5625f;
        Matrix.perspectiveM(proj, 0, FOV_Y, aspect, 0.5f, 30.0f);
        Matrix.setLookAtM(viewM, 0,
                0f, CAM_Y, CAM_Z,     // olho
                0f, TARGET_Y, 0f,     // alvo
                0f, 1f, 0f);          // up
        Matrix.multiplyMM(pv, 0, proj, 0, viewM, 0);
        setupTargets(w, h);
    }

    // =============================================================== frame
    @Override
    public void onDrawFrame(GL10 unused) {
        frameStartNs = System.nanoTime();
        long nowMs = SystemClock.uptimeMillis();
        float dt = lastFrameMs > 0 ? (nowMs - lastFrameMs) / 1000f : 0.016f;
        lastFrameMs = nowMs;
        if (dt > 0.1f) dt = 0.1f;
        animT += dt;

        if (!targetsReady && width > 0 && height > 0) {
            setupTargets(width, height);
        }

        updateExpression(nowMs);
        updateAnimation(dt);

        // ---------------- 1. cena -> alvo (MSAA quando disponível)
        int target = (msaaSamples > 0) ? msaaFbo : sceneFbo;
        GLES30.glBindFramebuffer(GLES30.GL_FRAMEBUFFER, target);
        GLES30.glViewport(0, 0, width, height);
        GLES30.glEnable(GLES30.GL_DEPTH_TEST);
        GLES30.glDepthMask(true);
        GLES30.glDisable(GLES30.GL_BLEND);
        GLES30.glClear(GLES30.GL_COLOR_BUFFER_BIT | GLES30.GL_DEPTH_BUFFER_BIT);
        GLES30.glUseProgram(program);
        GLES30.glUniform3fv(uCam, 1, camPos, 0);
        GLES30.glBindVertexArray(sceneVAO);

        drawScene(nowMs, dt);

        // ---------------- 2. resolve MSAA -> textura
        if (msaaSamples > 0) {
            GLES30.glBindFramebuffer(GLES30.GL_READ_FRAMEBUFFER, msaaFbo);
            GLES30.glBindFramebuffer(GLES30.GL_DRAW_FRAMEBUFFER, sceneFbo);
            GLES30.glBlitFramebuffer(0, 0, width, height, 0, 0, width, height,
                    GLES30.GL_COLOR_BUFFER_BIT, GLES30.GL_NEAREST);
            GLES30.glBindFramebuffer(GLES30.GL_FRAMEBUFFER, GLES30.GL_NONE);
        }

        // ---------------- 3. bloom (bright-pass 1/4 + gaussiana 4 passes)
        runBloom();

        // ---------------- 4. composite + FXAA -> framebuffer da janela
        runComposite();

        // ---------------- 5. limitador 30 FPS + telemetria
        long elapsedNs = System.nanoTime() - frameStartNs;
        long deadline = frameStartNs + TARGET_FRAME_NS - VSYNC_MARGIN_NS;
        long cur = System.nanoTime();
        if (deadline > cur) {
            long sleepMs = (deadline - cur) / 1_000_000L;
            int sleepNs = (int) ((deadline - cur) % 1_000_000L);
            try {
                Thread.sleep(sleepMs, sleepNs);
            } catch (InterruptedException ignored) {
            }
        }
        renderNsAccum += elapsedNs;
        fpsFrames++;
        long el = nowMs - fpsWindowStart;
        if (el >= 3000) {
            float fps = fpsFrames * 1000f / el;
            float renderMs = renderNsAccum / (float) fpsFrames / 1_000_000f;
            Log.i(TAG, "fps=" + String.format(java.util.Locale.US, "%.1f", fps)
                    + " renderMs=" + String.format(java.util.Locale.US, "%.1f", renderMs)
                    + " expr=" + currentExpr
                    + " msaa=" + msaaSamples
                    + " maxSamples=" + maxSamples
                    + " blink=" + String.format(java.util.Locale.US, "%.2f", blinkTarget));
            maybeAdaptiveQuality(fps, nowMs);
            fpsWindowStart = nowMs;
            fpsFrames = 0;
            renderNsAccum = 0;
        }
    }

    /**
     * Degradação determinística: se o alvo de 30 FPS não for atingido, reduz
     * 8x -> 4x -> 0 (FXAA puro). Nunca inventa suporte: só usa o que mediu.
     */
    private void maybeAdaptiveQuality(float fps, long nowMs) {
        if (fps >= 26f) return;
        if (nowMs - lastQualityChangeMs < 6000L) return;
        int before = msaaSamples;
        if (msaaSamples >= 8) {
            msaaSamples = 4;
        } else if (msaaSamples >= 4) {
            msaaSamples = 0;
        } else {
            return;
        }
        lastQualityChangeMs = nowMs;
        setupTargets(width, height);
        Log.w(TAG, "FX qualidade degradada: fps=" + String.format(java.util.Locale.US, "%.1f", fps)
                + " < 26 -> MSAA " + before + " -> " + msaaSamples
                + (msaaSamples == 0 ? " (anti-serrilhado via FXAA)" : ""));
    }

    // =============================================================== passes
    private void drawScene(long nowMs, float dt) {
        final float k = 1f - (float) Math.pow(0.88, dt * 60.0);

        // câmera dinâmica: afasta/alveja para baixo quando o busto aparece
        float camZd = CAM_Z + bodyAmt * 1.55f;
        float tgtYd = TARGET_Y - bodyAmt * 0.50f;
        Matrix.setLookAtM(viewM, 0, 0f, CAM_Y, camZd, 0f, tgtYd, 0f, 0f, 1f, 0f);
        Matrix.multiplyMM(pv, 0, proj, 0, viewM, 0);
        camPos[0] = 0f;
        camPos[1] = CAM_Y;
        camPos[2] = camZd;
        GLES30.glUniform3fv(uCam, 1, camPos, 0);

        // ---------------- raiz: escala + respiração + rotação da cabeça
        Matrix.setIdentityM(mHead, 0);
        Matrix.scaleM(mHead, 0, MODEL_SCALE, MODEL_SCALE, MODEL_SCALE);
        float breath = (float) Math.sin(animT * 1.45f) * 0.014f;
        Matrix.translateM(mHead, 0, 0f, breath, 0f);
        Matrix.rotateM(mHead, 0, headRoll * 57.2958f, 0f, 0f, 1f);
        Matrix.rotateM(mHead, 0, headYaw * 57.2958f, 0f, 1f, 0f);
        Matrix.rotateM(mHead, 0, headPitch * 57.2958f, 1f, 0f, 0f);

        Matrix.setIdentityM(mNeck, 0);
        Matrix.scaleM(mNeck, 0, MODEL_SCALE, MODEL_SCALE, MODEL_SCALE);
        Matrix.translateM(mNeck, 0, 0f, breath * 0.55f, 0f);

        float blinkScale = blinkTarget;
        float eyeYOff = gazeGY * 0.045f;
        float eyeXOff = gazeGX * 0.055f;

        // ---------------- passes opacos
        drawMesh(neckMesh, mNeck, 0, TINT_NECK, 1f);
        drawMesh(headMesh, mHead, 0, TINT_WHITE, 1f);
        drawMesh(earMesh, mHead, 0, TINT_WHITE, 1f);
        drawMesh(socketMesh, mHead, 0, TINT_SOCKET, 1f);

        // busto chibi (ombros/braços/mãos brancos) — só nos estados de corpo
        if (bodyAmt > 0.01f) {
            drawBody();
        }

        // sobrancelhas (neon projetadas no visor)
        float[][] browAnchors = {{-EYE_X, BROW_Y, browZ(-EYE_X)}, {EYE_X, BROW_Y, browZ(EYE_X)}};
        float[] browTilts = {browTiltL, browTiltR};
        float[] browRaises = {browRaiseL, browRaiseR};
        for (int e = 0; e < 2; e++) {
            Matrix.setIdentityM(mPart, 0);
            Matrix.translateM(mPart, 0, browAnchors[e][0], browAnchors[e][1] + browRaises[e], browAnchors[e][2]);
            Matrix.rotateM(mPart, 0, browTilts[e] * 57.2958f, 0f, 0f, 1f);
            Matrix.rotateM(mPart, 0, (e == 0 ? -20f : 20f), 0f, 1f, 0f);
            Matrix.scaleM(mPart, 0, 0.50f, 0.055f, 0.065f);
            Matrix.multiplyMM(tmp, 0, mHead, 0, mPart, 0);
            drawMesh(browBox, tmp, 1, eyeTint(0.85f), 1f);
        }

        // boca: 9 segmentos seguindo a curvatura do visor (emissiva)
        float segPitch = (2f * MOUTH_HALF) / (MOUTH_SEGS - 1);
        for (int i = 0; i < MOUTH_SEGS; i++) {
            float x = -MOUTH_HALF + i * segPitch;
            float y = MOUTH_Y + mouthK * x * x;
            float z = maskZ(x, y) + 0.030f;
            float h = 0.048f * (0.55f + mouthAmp * 3.2f);
            if (i == 0 || i == MOUTH_SEGS - 1) h *= 0.75f;
            float slopeDeg = (float) Math.toDegrees(Math.atan(2f * mouthK * x));
            Matrix.setIdentityM(mPart, 0);
            Matrix.translateM(mPart, 0, x, y, z);
            Matrix.rotateM(mPart, 0, slopeDeg, 0f, 0f, 1f);
            Matrix.scaleM(mPart, 0, segPitch * 1.35f, h, 0.055f);
            Matrix.multiplyMM(tmp, 0, mHead, 0, mPart, 0);
            drawMesh(mouthBox, tmp, 1, mouthTint(), 1f);
        }

        // olhos: anéis emissivos + pupila (olho direito fechado no PISCANDO;
        // olhos-estrela no EMPOLGADO)
        for (int e = 0; e < 2; e++) {
            float bsE = (winkAmt > 0.5f && e == 1) ? Math.min(blinkScale, 0.06f) : blinkScale;
            buildEyeMatrix(mEye, e, eyeXOff, eyeYOff, bsE);
            if (starAmt > 0.5f) {
                Matrix.translateM(mEye, 0, 0f, 0f, 0.02f);
                float stw = 1.28f + 0.06f * (float) Math.sin(animT * 4.2f + e);
                Matrix.scaleM(mEye, 0, stw, stw, 1f);
                drawMesh(starMesh, mEye, 1, eyeTint(1.35f), 1f);
            } else {
                drawMesh(ringOuter, mEye, 1, eyeTint(1f), 1f);
                drawMesh(ringMid, mEye, 1, eyeTint(1f), 1f);
                drawMesh(ringCore, mEye, 1, eyeTint(1f), 1f);
                Matrix.translateM(mEye, 0, 0f, 0f, 0.004f);
                drawMesh(pupilMesh, mEye, 1, new float[]{0.02f, 0.03f, 0.045f}, 1f);
            }
        }

        // ---------------- passes aditivos (aura/halo/corações)
        GLES30.glEnable(GLES30.GL_BLEND);
        GLES30.glBlendFunc(GLES30.GL_ONE, GLES30.GL_ONE);
        GLES30.glDepthMask(false);

        // aura de fundo ligada à cor do estado
        Matrix.setIdentityM(mAura, 0);
        Matrix.translateM(mAura, 0, 0f, 0.12f, -1.35f);
        Matrix.scaleM(mAura, 0, 2.9f, 2.9f, 1f);
        float auraA = 0.030f + 0.012f * (float) Math.sin(animT * 1.1f);
        if ("escutando".equals(currentExpr) || "escuta_ativa".equals(currentExpr)) {
            auraA += 0.025f;
        }
        drawMesh(auraMesh, mAura, 1, eyeTint(1f), Math.max(0.01f, auraA));

        // halo dos olhos
        float pulse = ("escutando".equals(currentExpr) || "escuta_ativa".equals(currentExpr))
                ? 1f + 0.28f * (float) Math.sin(animT * 5.0f)
                : 1f;
        for (int e = 0; e < 2; e++) {
            buildEyeMatrix(mEye, e, eyeXOff, eyeYOff, Math.max(blinkScale, 0.15f));
            Matrix.scaleM(mEye, 0, 1.95f * pulse, 1.95f * pulse, 1f);
            Matrix.translateM(mEye, 0, 0f, 0f, 0.005f);
            drawMesh(haloMesh, mEye, 1, eyeTint(1f), 1.3f);
        }

        // anel emissivo das orelhas
        drawMesh(earGlowMesh, mHead, 1, eyeTint(0.55f), 1f);

        // corações flutuantes (estado CARINHOSO — rosa/magenta)
        if (heartAmt > 0.01f) {
            for (int hi = 0; hi < 3; hi++) {
                float phase = (animT * 0.34f + hi * 0.37f) % 1f;
                float hx = (hi == 0 ? -1.15f : (hi == 1 ? 1.15f : 0.02f));
                float hy = -0.35f + phase * 1.5f;
                float hs = (hi == 2 ? 0.10f : 0.145f) * (0.85f + 0.2f * (float) Math.sin(animT * 3.1f + hi));
                float fade = (phase < 0.15f ? phase / 0.15f : 1f) * (1f - phase * 0.75f);
                float a = 0.75f * fade * heartAmt;
                if (a <= 0.01f) continue;
                Matrix.setIdentityM(mPart, 0);
                Matrix.translateM(mPart, 0, hx, hy, 1.55f);
                Matrix.rotateM(mPart, 0, (float) Math.sin(animT * 1.3f + hi * 2f) * 9f, 0f, 0f, 1f);
                Matrix.scaleM(mPart, 0, hs, hs, 1f);
                drawMesh(heartMesh, mPart, 1, TINT_PINK, a);
            }
        }

        // ---------------- extras animados do sprite sheet (aditivos)
        drawExtras();

        GLES30.glDepthMask(true);
        GLES30.glDisable(GLES30.GL_BLEND);
    }

    private void runBloom() {
        GLES30.glDisable(GLES30.GL_DEPTH_TEST);
        GLES30.glDisable(GLES30.GL_BLEND);
        GLES30.glBindVertexArray(postVAO);
        bindPostAttribs();

        // bright-pass (downsample 1/4 + máscara de saturação)
        GLES30.glBindFramebuffer(GLES30.GL_FRAMEBUFFER, brightFbo);
        GLES30.glViewport(0, 0, bloomW, bloomH);
        GLES30.glUseProgram(progBright);
        GLES30.glActiveTexture(GLES30.GL_TEXTURE0);
        GLES30.glBindTexture(GLES30.GL_TEXTURE_2D, sceneTex);
        GLES30.glUniform1i(uBrightScene, 0);
        GLES30.glUniform3f(uBrightThr, 0.30f, 0.55f, 0.35f);
        GLES30.glDrawArrays(GLES30.GL_TRIANGLES, 0, 3);

        // gaussiana H,V,H,V (raio amplo = bloom suave)
        GLES30.glUseProgram(progBlur);
        GLES30.glUniform1i(uBlurTex, 0);
        passBlur(blurFboA, brightTex, 1.6f / bloomW, 0f);
        passBlur(blurFboB, blurTexA, 0f, 1.6f / bloomH);
        passBlur(blurFboA, blurTexB, 3.4f / bloomW, 0f);
        passBlur(blurFboB, blurTexA, 0f, 3.4f / bloomH);
    }

    private void passBlur(int dstFbo, int srcTex, float dx, float dy) {
        GLES30.glBindFramebuffer(GLES30.GL_FRAMEBUFFER, dstFbo);
        GLES30.glBindTexture(GLES30.GL_TEXTURE_2D, srcTex);
        GLES30.glUniform2f(uBlurDir, dx, dy);
        GLES30.glDrawArrays(GLES30.GL_TRIANGLES, 0, 3);
    }

    private void runComposite() {
        GLES30.glBindFramebuffer(GLES30.GL_FRAMEBUFFER, GLES30.GL_NONE);
        GLES30.glViewport(0, 0, width, height);
        GLES30.glUseProgram(progComposite);
        GLES30.glActiveTexture(GLES30.GL_TEXTURE0);
        GLES30.glBindTexture(GLES30.GL_TEXTURE_2D, sceneTex);
        GLES30.glUniform1i(uCompScene, 0);
        GLES30.glActiveTexture(GLES30.GL_TEXTURE1);
        GLES30.glBindTexture(GLES30.GL_TEXTURE_2D, blurTexB);
        GLES30.glUniform1i(uCompBloom, 1);
        GLES30.glUniform2f(uCompTexel, 1f / width, 1f / height);
        GLES30.glUniform2f(uCompPost, 0.62f, 0.42f);
        GLES30.glActiveTexture(GLES30.GL_TEXTURE0);
        GLES30.glDrawArrays(GLES30.GL_TRIANGLES, 0, 3);
    }

    private void bindPostAttribs() {
        GLES30.glBindBuffer(GLES30.GL_ARRAY_BUFFER, postVBO);
        GLES30.glVertexAttribPointer(0, 2, GLES30.GL_FLOAT, false, 8, 0);
        GLES30.glEnableVertexAttribArray(0);
        GLES30.glDisableVertexAttribArray(1);
        GLES30.glDisableVertexAttribArray(2);
    }

    // =============================================================== alvos
    private void setupTargets(int w, int h) {
        targetsReady = false;
        teardownTargets();
        if (w <= 0 || h <= 0) return;

        // ---- alvo de resolve/cena (textura RGBA8 + depth16)
        sceneTex = makeTexture(w, h);
        int[] rb = new int[1];
        GLES30.glGenRenderbuffers(1, rb, 0);
        sceneDepthRb = rb[0];
        GLES30.glBindRenderbuffer(GLES30.GL_RENDERBUFFER, sceneDepthRb);
        GLES30.glRenderbufferStorage(GLES30.GL_RENDERBUFFER, GLES30.GL_DEPTH_COMPONENT16, w, h);

        int[] f = new int[1];
        GLES30.glGenFramebuffers(1, f, 0);
        sceneFbo = f[0];
        GLES30.glBindFramebuffer(GLES30.GL_FRAMEBUFFER, sceneFbo);
        GLES30.glFramebufferTexture2D(GLES30.GL_FRAMEBUFFER, GLES30.GL_COLOR_ATTACHMENT0,
                GLES30.GL_TEXTURE_2D, sceneTex, 0);
        GLES30.glFramebufferRenderbuffer(GLES30.GL_FRAMEBUFFER, GLES30.GL_DEPTH_ATTACHMENT,
                GLES30.GL_RENDERBUFFER, sceneDepthRb);
        checkFbo("scene");

        // ---- alvo MSAA (só se a GPU realmente suportar e o FBO fechar)
        if (msaaSamples > 0) {
            int[] rb2 = new int[2];
            GLES30.glGenRenderbuffers(2, rb2, 0);
            msaaColorRb = rb2[0];
            msaaDepthRb = rb2[1];
            GLES30.glBindRenderbuffer(GLES30.GL_RENDERBUFFER, msaaColorRb);
            GLES30.glRenderbufferStorageMultisample(GLES30.GL_RENDERBUFFER, msaaSamples,
                    GLES30.GL_RGBA8, w, h);
            GLES30.glBindRenderbuffer(GLES30.GL_RENDERBUFFER, msaaDepthRb);
            GLES30.glRenderbufferStorageMultisample(GLES30.GL_RENDERBUFFER, msaaSamples,
                    GLES30.GL_DEPTH_COMPONENT16, w, h);
            GLES30.glGenFramebuffers(1, f, 0);
            msaaFbo = f[0];
            GLES30.glBindFramebuffer(GLES30.GL_FRAMEBUFFER, msaaFbo);
            GLES30.glFramebufferRenderbuffer(GLES30.GL_FRAMEBUFFER, GLES30.GL_COLOR_ATTACHMENT0,
                    GLES30.GL_RENDERBUFFER, msaaColorRb);
            GLES30.glFramebufferRenderbuffer(GLES30.GL_FRAMEBUFFER, GLES30.GL_DEPTH_ATTACHMENT,
                    GLES30.GL_RENDERBUFFER, msaaDepthRb);
            int status = GLES30.glCheckFramebufferStatus(GLES30.GL_FRAMEBUFFER);
            if (status != GLES30.GL_FRAMEBUFFER_COMPLETE) {
                Log.e(TAG, "FX MSAA FBO incompleto status=0x" + Integer.toHexString(status)
                        + " com samples=" + msaaSamples + " -> fallback FXAA (sem MSAA)");
                GLES30.glDeleteFramebuffers(1, new int[]{msaaFbo}, 0);
                GLES30.glDeleteRenderbuffers(2, new int[]{msaaColorRb, msaaDepthRb}, 0);
                msaaFbo = 0;
                msaaColorRb = 0;
                msaaDepthRb = 0;
                msaaSamples = 0;
            } else {
                Log.i(TAG, "FX MSAA OK: samples=" + msaaSamples + " em " + w + "x" + h
                        + " (GL_MAX_SAMPLES=" + maxSamples + ")");
            }
        }

        // ---- bloom em 1/4 da resolução
        bloomW = Math.max(1, w / 4);
        bloomH = Math.max(1, h / 4);
        brightTex = makeTexture(bloomW, bloomH);
        brightFbo = makeFboForTex(brightTex, "bright");
        blurTexA = makeTexture(bloomW, bloomH);
        blurFboA = makeFboForTex(blurTexA, "blurA");
        blurTexB = makeTexture(bloomW, bloomH);
        blurFboB = makeFboForTex(blurTexB, "blurB");

        GLES30.glBindFramebuffer(GLES30.GL_FRAMEBUFFER, GLES30.GL_NONE);
        GLES30.glViewport(0, 0, w, h);
        targetsReady = true;
        Log.i(TAG, "FX alvos: scene=" + w + "x" + h + " bloom=" + bloomW + "x" + bloomH
                + " msaa=" + msaaSamples);
    }

    private void teardownTargets() {
        if (sceneFbo != 0) GLES30.glDeleteFramebuffers(1, new int[]{sceneFbo}, 0);
        if (msaaFbo != 0) GLES30.glDeleteFramebuffers(1, new int[]{msaaFbo}, 0);
        if (brightFbo != 0) GLES30.glDeleteFramebuffers(1, new int[]{brightFbo}, 0);
        if (blurFboA != 0) GLES30.glDeleteFramebuffers(1, new int[]{blurFboA}, 0);
        if (blurFboB != 0) GLES30.glDeleteFramebuffers(1, new int[]{blurFboB}, 0);
        int[] texs = new int[4];
        int alive = 0;
        if (sceneTex != 0) texs[alive++] = sceneTex;
        if (brightTex != 0) texs[alive++] = brightTex;
        if (blurTexA != 0) texs[alive++] = blurTexA;
        if (blurTexB != 0) texs[alive++] = blurTexB;
        if (alive > 0) GLES30.glDeleteTextures(alive, texs, 0);
        int[] rbs = new int[3];
        int ar = 0;
        if (sceneDepthRb != 0) rbs[ar++] = sceneDepthRb;
        if (msaaColorRb != 0) rbs[ar++] = msaaColorRb;
        if (msaaDepthRb != 0) rbs[ar++] = msaaDepthRb;
        if (ar > 0) GLES30.glDeleteRenderbuffers(ar, rbs, 0);
        sceneFbo = msaaFbo = brightFbo = blurFboA = blurFboB = 0;
        sceneTex = brightTex = blurTexA = blurTexB = 0;
        sceneDepthRb = msaaColorRb = msaaDepthRb = 0;
        targetsReady = false;
    }

    private int makeTexture(int w, int h) {
        int[] t = new int[1];
        GLES30.glGenTextures(1, t, 0);
        GLES30.glBindTexture(GLES30.GL_TEXTURE_2D, t[0]);
        GLES30.glTexImage2D(GLES30.GL_TEXTURE_2D, 0, GLES30.GL_RGBA8, w, h, 0,
                GLES30.GL_RGBA, GLES30.GL_UNSIGNED_BYTE, null);
        GLES30.glTexParameteri(GLES30.GL_TEXTURE_2D, GLES30.GL_TEXTURE_MIN_FILTER, GLES30.GL_LINEAR);
        GLES30.glTexParameteri(GLES30.GL_TEXTURE_2D, GLES30.GL_TEXTURE_MAG_FILTER, GLES30.GL_LINEAR);
        GLES30.glTexParameteri(GLES30.GL_TEXTURE_2D, GLES30.GL_TEXTURE_WRAP_S, GLES30.GL_CLAMP_TO_EDGE);
        GLES30.glTexParameteri(GLES30.GL_TEXTURE_2D, GLES30.GL_TEXTURE_WRAP_T, GLES30.GL_CLAMP_TO_EDGE);
        return t[0];
    }

    private int makeFboForTex(int tex, String tag) {
        int[] f = new int[1];
        GLES30.glGenFramebuffers(1, f, 0);
        GLES30.glBindFramebuffer(GLES30.GL_FRAMEBUFFER, f[0]);
        GLES30.glFramebufferTexture2D(GLES30.GL_FRAMEBUFFER, GLES30.GL_COLOR_ATTACHMENT0,
                GLES30.GL_TEXTURE_2D, tex, 0);
        checkFbo(tag);
        return f[0];
    }

    private void checkFbo(String tag) {
        int status = GLES30.glCheckFramebufferStatus(GLES30.GL_FRAMEBUFFER);
        if (status != GLES30.GL_FRAMEBUFFER_COMPLETE) {
            Log.e(TAG, "FBO '" + tag + "' INCOMPLETO: 0x" + Integer.toHexString(status));
        }
    }

    // =============================================================== animação
    private void updateExpression(long nowMs) {
        long lastExt = view.lastExternalUpdateMs;
        if (nowMs - lastExt > IPC_GRACE_MS) {
            if (lastDemoStepMs == 0) lastDemoStepMs = nowMs;
            if (nowMs - lastDemoStepMs >= DEMO_STEP_MS) {
                demoIdx = (demoIdx + 1) % DEMO_STATES.length;
                lastDemoStepMs = nowMs;
            }
            currentExpr = DEMO_STATES[demoIdx];
        } else {
            currentExpr = normalize(view.expression);
        }

        // --- cor emissiva (família verde-menta neon; rosa/magenta no CARINHOSO)
        switch (currentExpr) {
            case "carinhoso":  eyeTarget[0] = 1.00f; eyeTarget[1] = 0.26f; eyeTarget[2] = 0.62f; break;
            case "triste":     eyeTarget[0] = 0.16f; eyeTarget[1] = 0.72f; eyeTarget[2] = 0.56f; break;
            case "preocupado": eyeTarget[0] = 0.20f; eyeTarget[1] = 0.80f; eyeTarget[2] = 0.58f; break;
            case "irritado":   eyeTarget[0] = 0.34f; eyeTarget[1] = 1.00f; eyeTarget[2] = 0.52f; break;
            case "sonolento":  eyeTarget[0] = 0.13f; eyeTarget[1] = 0.62f; eyeTarget[2] = 0.46f; break;
            case "relaxado":   eyeTarget[0] = 0.18f; eyeTarget[1] = 0.78f; eyeTarget[2] = 0.60f; break;
            case "surpreso":   eyeTarget[0] = 0.30f; eyeTarget[1] = 1.00f; eyeTarget[2] = 0.82f; break;
            case "confuso":    eyeTarget[0] = 0.22f; eyeTarget[1] = 0.92f; eyeTarget[2] = 0.88f; break;
            case "cetico":     eyeTarget[0] = 0.20f; eyeTarget[1] = 0.86f; eyeTarget[2] = 0.70f; break;
            case "pensativo":  eyeTarget[0] = 0.24f; eyeTarget[1] = 0.88f; eyeTarget[2] = 0.70f; break;
            case "processando":eyeTarget[0] = 0.24f; eyeTarget[1] = 0.92f; eyeTarget[2] = 0.72f; break;
            case "empolgado":  eyeTarget[0] = 0.28f; eyeTarget[1] = 1.00f; eyeTarget[2] = 0.78f; break;
            case "alegre":     eyeTarget[0] = 0.26f; eyeTarget[1] = 1.00f; eyeTarget[2] = 0.76f; break;
            case "hud":        eyeTarget[0] = 0.24f; eyeTarget[1] = 1.00f; eyeTarget[2] = 0.80f; break;
            default:           eyeTarget[0] = 0.24f; eyeTarget[1] = 1.00f; eyeTarget[2] = 0.75f; break; // menta
        }

        // --- cabeça / sobrancelhas / boca / blink / gaze
        tYaw = (float) Math.sin(animT * 0.33f) * 0.09f;
        tPitch = (float) Math.sin(animT * 0.21f) * 0.045f;
        tRoll = 0f;
        tBrowTL = 0f; tBrowTR = 0f; tBrowRL = 0f; tBrowRR = 0f;
        tMouthK = 0.40f;      // sorriso neutro suave
        tBlink = 1f;
        tGazeX = (float) Math.sin(animT * 0.45f) * 0.22f;
        tGazeY = (float) Math.sin(animT * 0.31f + 1.2f) * 0.16f;
        tEyeScale = 1f;
        tHeart = 0f;
        tBody = 0f; tWink = 0f; tStar = 0f; tZzz = 0f; tQuest = 0f;
        tHolo = 0f; tBulb = 0f; tConfetti = 0f; tWave = 0f;
        tArmLL = 0.10f; tArmLR = 0.10f; tArmFL = 0f; tArmFR = 0f;
        tArmEL = -0.25f; tArmER = -0.25f;

        switch (currentExpr) {
            case "feliz":
                tBrowTL = -0.10f; tBrowTR = 0.10f;
                tBrowRL = 0.05f; tBrowRR = 0.05f;
                tMouthK = 1.75f;
                tBlink = 0.52f;               // olhos ^ (achatados)
                tEyeScale = 1.05f;
                break;
            case "focado":
                tBrowTL = -0.34f; tBrowTR = 0.34f;   // V (internos p/ baixo)
                tBlink = 0.92f;
                tGazeX = 0f; tGazeY = 0.04f;
                tMouthK = 0.0f;
                break;
            case "confuso":
                tBrowTL = 0.16f; tBrowRL = 0.065f;
                tRoll = (float) Math.sin(animT * 1.3f) * 0.09f;
                tMouthK = -0.35f;
                tEyeScale = 0.97f;
                tQuest = 1f;
                break;
            case "sonolento":
                tBlink = 0.07f;
                tPitch += 0.14f;
                tBrowRL = -0.05f; tBrowRR = -0.05f;
                tGazeX = 0f; tGazeY = -0.30f;
                tMouthK = -0.15f;
                tZzz = 1f;
                break;
            case "carinhoso":
                tBrowTL = -0.06f; tBrowTR = 0.06f;
                tBrowRL = 0.05f; tBrowRR = 0.05f;
                tMouthK = 1.40f;
                tBlink = 0.72f;
                tHeart = 1f;
                tRoll = (float) Math.sin(animT * 0.7f) * 0.05f;
                break;
            case "triste":
                tBrowTL = 0.30f; tBrowTR = -0.30f;   // internos p/ cima
                tBrowRL = -0.045f; tBrowRR = -0.045f;
                tPitch += 0.11f;
                tMouthK = -1.30f;
                tBlink = 0.85f;
                tGazeY = -0.25f;
                break;
            case "surpreso":
                tBrowRL = 0.13f; tBrowRR = 0.13f;
                tBrowTL = -0.05f; tBrowTR = 0.05f;
                tEyeScale = 1.28f;
                tMouthK = 0f;
                tGazeX = 0f; tGazeY = 0.06f;
                tBlink = 1.10f;
                break;
            case "falando":
                tBrowRL = 0.03f; tBrowRR = 0.03f;
                tMouthK = 0.30f;
                break;
            case "escutando":
                tBrowRL = 0.055f; tBrowRR = 0.055f;
                tGazeX = 0f; tGazeY = 0.05f;
                tEyeScale = 1.10f;
                tMouthK = 0.35f;
                tYaw = 0.22f;
                break;
            case "pensativo":
                tBrowTR = 0.06f;
                tGazeX = 0.50f;
                tGazeY = 0.42f + (float) Math.sin(animT * 0.9f) * 0.10f;
                tMouthK = -0.10f;
                break;
            case "alegre":
                tBrowTL = -0.12f; tBrowTR = 0.12f;
                tBrowRL = 0.07f; tBrowRR = 0.07f;
                tMouthK = 1.30f;
                tBlink = 0.34f;
                tEyeScale = 1.08f;
                tGazeY = 0.05f;
                break;
            case "piscando":
                tBrowTL = -0.08f; tBrowTR = 0.08f;
                tBrowRL = 0.04f; tBrowRR = 0.04f;
                tMouthK = 1.45f;
                tWink = 1f;
                break;
            case "saudacao":
                tRoll = 0.10f + (float) Math.sin(animT * 1.6f) * 0.04f;
                tBrowRL = 0.05f; tBrowRR = 0.05f;
                tMouthK = 1.20f;
                tEyeScale = 1.04f;
                break;
            case "curioso":
                tEyeScale = 1.16f;
                tBrowRL = 0.08f; tBrowRR = 0.06f;
                tRoll = (float) Math.sin(animT * 1.1f) * 0.12f;
                tMouthK = 0.35f;
                tGazeX = 0.28f + (float) Math.sin(animT * 0.8f) * 0.15f;
                break;
            case "empolgado":
                tStar = 1f;
                tBrowRL = 0.12f; tBrowRR = 0.12f;
                tMouthK = 1.55f;
                tEyeScale = 1.12f;
                break;
            case "cetico":
                tBlink = 0.52f;
                tBrowTL = 0.20f; tBrowTR = -0.14f;
                tBrowRL = 0.02f; tBrowRR = 0.0f;
                tMouthK = -0.25f;
                tGazeX = 0.18f;
                tYaw = 0.06f;
                break;
            case "preocupado":
                tBrowTL = 0.22f; tBrowTR = -0.22f;
                tBrowRL = -0.03f; tBrowRR = -0.03f;
                tMouthK = -0.40f;
                tBlink = 0.88f;
                tGazeY = -0.12f;
                tRoll = (float) Math.sin(animT * 1.9f) * 0.03f;
                break;
            case "irritado":
                tBrowTL = -0.42f; tBrowTR = 0.42f;
                tBrowRL = 0.02f; tBrowRR = 0.02f;
                tMouthK = -1.15f;
                tBlink = 0.80f;
                tGazeX = 0f; tGazeY = 0.02f;
                tEyeScale = 0.94f;
                break;
            case "confiante":
                tBrowTL = -0.10f; tBrowTR = 0.10f;
                tBrowRL = 0.06f; tBrowRR = 0.06f;
                tMouthK = 1.60f;
                tBlink = 0.55f;
                tRoll = -0.05f;
                break;
            case "relaxado":
                tBlink = 0.10f;
                tBrowRL = 0.02f; tBrowRR = 0.02f;
                tMouthK = 0.85f;
                tPitch += 0.05f;
                tGazeX = 0f; tGazeY = 0f;
                break;
            case "saudacao_completa":
                tBody = 1f;
                tBrowTL = -0.10f; tBrowTR = 0.10f;
                tBrowRL = 0.06f; tBrowRR = 0.06f;
                tMouthK = 1.50f;
                tBlink = 0.55f;
                tYaw = (float) Math.sin(animT * 0.8f) * 0.10f;
                tArmLL = 0.12f; tArmFL = 0f; tArmEL = -0.30f;
                tArmLR = 2.35f; tArmFR = -0.25f;
                tArmER = -0.55f + (float) Math.sin(animT * 5.5f) * 0.45f;
                break;
            case "hud":
                tBody = 1f;
                tHolo = 1f;
                tBrowRL = 0.05f; tBrowRR = 0.05f;
                tMouthK = 0.30f;
                tGazeX = 0.12f; tGazeY = 0.08f;
                tArmLL = 0.55f; tArmFL = -1.25f; tArmEL = -0.55f;
                tArmLR = 0.55f; tArmFR = -1.25f; tArmER = -0.55f;
                break;
            case "processando":
                tBody = 1f;
                tBulb = 1f;
                tBrowTR = 0.07f;
                tGazeX = 0.30f;
                tGazeY = 0.45f + (float) Math.sin(animT * 0.9f) * 0.10f;
                tMouthK = -0.10f;
                tArmLL = 0.20f; tArmFL = -0.15f; tArmEL = -0.40f;
                tArmLR = -1.95f; tArmFR = -0.55f; tArmER = -1.15f;
                break;
            case "comemoracao":
                tBody = 1f;
                tConfetti = 1f;
                tBrowRL = 0.10f; tBrowRR = 0.10f;
                tMouthK = 1.35f;
                tBlink = 0.30f;
                tEyeScale = 1.06f;
                tPitch -= 0.06f;
                tArmLL = -2.45f; tArmFL = -0.10f; tArmEL = -0.15f;
                tArmLR = 2.45f; tArmFR = -0.10f; tArmER = -0.15f;
                break;
            case "escuta_ativa":
                tBody = 1f;
                tWave = 1f;
                tBrowRL = 0.07f; tBrowRR = 0.07f;
                tEyeScale = 1.10f;
                tYaw = 0.55f + (float) Math.sin(animT * 0.7f) * 0.05f;
                tMouthK = 0.30f;
                tArmLL = 0.16f; tArmFL = -0.08f; tArmEL = -0.30f;
                tArmLR = 0.16f; tArmFR = -0.08f; tArmER = -0.30f;
                break;
            default: // padrao
                break;
        }

        // gaze externo (IPC) sobrepõe o automático quando presente
        if (view.lookX != 0f) tGazeX = view.lookX;
        if (view.lookY != 0f) tGazeY = view.lookY;

        // boca: amplitude de fala (IPC ou sintética) + bocas abertas por estado
        float amp = view.amplitude;
        float openT;
        if ("falando".equals(currentExpr)) {
            if (amp < 0.03f) {
                amp = 0.45f + 0.30f * (float) Math.sin(animT * 9.0f)
                        + 0.18f * (float) Math.sin(animT * 13.7f);
                amp = Math.max(0f, Math.min(1f, amp));
            }
            openT = amp;
        } else if ("surpreso".equals(currentExpr)) {
            openT = 0.52f;
        } else if ("alegre".equals(currentExpr)) {
            openT = 0.55f + 0.08f * (float) Math.sin(animT * 2.4f);
        } else if ("empolgado".equals(currentExpr)) {
            openT = 0.50f;
        } else if ("comemoracao".equals(currentExpr)) {
            openT = 0.58f + 0.10f * (float) Math.sin(animT * 3.1f);
        } else if ("feliz".equals(currentExpr)) {
            openT = 0.22f;
        } else if ("carinhoso".equals(currentExpr)) {
            openT = 0.26f;
        } else if ("saudacao".equals(currentExpr)
                || "saudacao_completa".equals(currentExpr)) {
            openT = 0.16f;
        } else {
            openT = 0f;
        }
        float mLerp = "falando".equals(currentExpr) ? 0.4f : 0.15f;
        mouthAmp += (openT - mouthAmp) * mLerp;
    }

    /** Mapeia qualquer estado recebido (IPC Rust, PT-BR, sheet) aos 26 canônicos. */
    private static String normalize(String raw) {
        if (raw == null) return "padrao";
        String e = raw.trim().toLowerCase(java.util.Locale.ROOT)
                .replace(' ', '_').replace('-', '_');
        switch (e) {
            case "idle": case "default": case "padrao": case "neutro": case "none":
            case "": return "padrao";
            case "happy": case "feliz": case "smile": return "feliz";
            case "alegre": case "very_happy": case "muito_satisfeito": return "alegre";
            case "wink": case "piscando": case "winking": return "piscando";
            case "saudacao": case "hello": case "hi": case "wave": case "greet":
                return "saudacao";
            case "curioso": case "curious": case "explorando": return "curioso";
            case "pensativo": case "thinking": case "analizando": case "analisando":
            case "analyzing": return "pensativo";
            case "falando": case "speaking": case "talking": case "talk":
                return "falando";
            case "escutando": case "escuta": case "listening": case "ouvindo":
            case "hear": case "wake": return "escutando";
            case "focado": case "focused": case "alert": case "foco":
            case "executando": return "focado";
            case "surpreso": case "surprised": case "shock": return "surpreso";
            case "empolgado": case "excited": case "star": return "empolgado";
            case "confuso": case "confused": case "precisa_de_detalhes":
                return "confuso";
            case "cetico": case "skeptical": case "duvida": case "cynical":
                return "cetico";
            case "triste": case "sad": return "triste";
            case "preocupado": case "worried": case "concerned":
                return "preocupado";
            case "irritado": case "angry": case "mad": case "rage":
            case "glitch": case "error": case "erro": case "bloqueio":
                return "irritado";
            case "confiante": case "confidente": case "confident":
            case "thumbs_up": case "thumbsup": return "confiante";
            case "relaxado": case "relaxed": case "calm": case "repouso":
                return "relaxado";
            case "sonolento": case "sleeping": case "dormindo": case "rest":
            case "resting": case "sleep": return "sonolento";
            case "carinhoso": case "love": case "affectionate": case "amor":
            case "cute": return "carinhoso";
            case "saudacao_completa": case "greeting_full": case "aceno":
            case "hello_full": return "saudacao_completa";
            case "hud": case "interacao": case "interacao_com_interface":
            case "interface": case "hologram": case "hologramas": case "ui":
                return "hud";
            case "processando": case "processing": case "working": case "idea":
                return "processando";
            case "comemoracao": case "celebration": case "celebrate":
            case "victory": case "party": return "comemoracao";
            case "escuta_ativa": case "active_listening": case "onda":
            case "modo_escuta_ativa": return "escuta_ativa";
            default: return "padrao";
        }
    }

    private void updateAnimation(float dt) {
        final float k = 1f - (float) Math.pow(0.88, dt * 60.0);

        for (int i = 0; i < 3; i++) {
            eyeCur[i] += (eyeTarget[i] - eyeCur[i]) * Math.min(1f, dt * 6f);
        }
        gazeGX += (tGazeX - gazeGX) * Math.min(1f, dt * 5f);
        gazeGY += (tGazeY - gazeGY) * Math.min(1f, dt * 5f);
        browTiltL += (tBrowTL - browTiltL) * k;
        browTiltR += (tBrowTR - browTiltR) * k;
        browRaiseL += (tBrowRL - browRaiseL) * k;
        browRaiseR += (tBrowRR - browRaiseR) * k;
        mouthK += (tMouthK - mouthK) * Math.min(1f, dt * 6f);
        headYaw += (tYaw - headYaw) * Math.min(1f, dt * 4f);
        headPitch += (tPitch - headPitch) * Math.min(1f, dt * 4f);
        headRoll += (tRoll - headRoll) * Math.min(1f, dt * 4f);
        eyeScale += (tEyeScale - eyeScale) * Math.min(1f, dt * 6f);
        heartAmt += (tHeart - heartAmt) * Math.min(1f, dt * 3f);
        bodyAmt += (tBody - bodyAmt) * Math.min(1f, dt * 3f);
        winkAmt += (tWink - winkAmt) * Math.min(1f, dt * 8f);
        starAmt += (tStar - starAmt) * Math.min(1f, dt * 6f);
        zzzAmt += (tZzz - zzzAmt) * Math.min(1f, dt * 3f);
        questAmt += (tQuest - questAmt) * Math.min(1f, dt * 4f);
        holoAmt += (tHolo - holoAmt) * Math.min(1f, dt * 4f);
        bulbAmt += (tBulb - bulbAmt) * Math.min(1f, dt * 4f);
        confettiAmt += (tConfetti - confettiAmt) * Math.min(1f, dt * 4f);
        waveAmt += (tWave - waveAmt) * Math.min(1f, dt * 4f);
        armLL += (tArmLL - armLL) * Math.min(1f, dt * 4f);
        armLR += (tArmLR - armLR) * Math.min(1f, dt * 4f);
        armFL += (tArmFL - armFL) * Math.min(1f, dt * 4f);
        armFR += (tArmFR - armFR) * Math.min(1f, dt * 4f);
        armEL += (tArmEL - armEL) * Math.min(1f, dt * 4f);
        armER += (tArmER - armER) * Math.min(1f, dt * 4f);

        // piscar automático (~4,5s) ou sob demanda via IPC
        blinkTimer += dt;
        if (view.blinkRequest || blinkTimer > 4.5f) {
            view.blinkRequest = false;
            blinkTimer = 0f;
            blinkPhase = 0.01f;
        }
        float bs = 1f;
        if (blinkPhase > 0f) {
            blinkPhase += dt * 7.2f; // ~140ms
            if (blinkPhase >= 1f) blinkPhase = 0f;
            bs = (float) Math.abs(Math.cos(blinkPhase * Math.PI));
            if (bs < 0.03f) bs = 0.03f;
        }
        if (tBlink < bs) bs = tBlink;
        if (tBlink > 1f && blinkPhase == 0f) bs = tBlink;
        blinkTarget = bs;
    }

    // =============================================================== helpers
    private float[] eyeTint(float mul) {
        return new float[]{eyeCur[0] * mul, eyeCur[1] * mul, eyeCur[2] * mul};
    }

    private float[] mouthTint() {
        float base = 0.70f + 0.55f * Math.min(1f, mouthAmp * 1.6f);
        return new float[]{
                Math.min(1.4f, eyeCur[0] * base),
                Math.min(1.4f, eyeCur[1] * base),
                Math.min(1.4f, eyeCur[2] * base)};
    }

    /** Superfície frontal do VISOOR (elipsoide * MASK) em (x, y). */
    private static float maskZ(float x, float y) {
        float a = RX * MASK, b = RY * MASK, c = RZ * MASK;
        float q = 1f - (x * x) / (a * a) - (y * y) / (b * b);
        if (q < 0.01f) q = 0.01f;
        return c * (float) Math.sqrt(q);
    }

    private static float browZ(float x) {
        return maskZ(x, BROW_Y) + 0.038f;
    }

    private static float eyeZ() {
        return maskZ(EYE_X, EYE_Y);
    }

    /**
     * Matriz do olho: ponto na superfície + base (t,b,n) + offset do gaze +
     * squash vertical do piscar + escala do estado + empilhamento por anel.
     * e=0 esquerdo (x negativo), e=1 direito.
     */
    private void buildEyeMatrix(float[] out, int e, float ox, float oy, float blinkScale) {
        float sx = (e == 0 ? -EYE_X : EYE_X);
        float sy = EYE_Y;
        float z = eyeZ();
        float a = RX * MASK, b = RY * MASK, c = RZ * MASK;
        float nx = sx / (a * a), ny = sy / (b * b), nz = z / (c * c);
        float len = (float) Math.sqrt(nx * nx + ny * ny + nz * nz);
        nx /= len; ny /= len; nz /= len;
        float tx = nz, ty = 0f, tz = -nx;
        float tl = (float) Math.sqrt(tx * tx + ty * ty + tz * tz);
        if (tl < 1e-4f) { tx = 1f; ty = 0f; tz = 0f; tl = 1f; }
        tx /= tl; ty /= tl; tz /= tl;
        float bx = ny * tz - nz * ty;
        float by = nz * tx - nx * tz;
        float bz = nx * ty - ny * tx;

        Matrix.setIdentityM(out, 0);
        out[0] = tx; out[1] = ty; out[2] = tz; out[3] = 0f;
        out[4] = bx; out[5] = by; out[6] = bz; out[7] = 0f;
        out[8] = nx; out[9] = ny; out[10] = nz; out[11] = 0f;
        out[12] = sx; out[13] = sy; out[14] = z; out[15] = 1f;
        Matrix.translateM(out, 0, ox, oy, 0.012f);
        Matrix.scaleM(out, 0, EYE_R * eyeScale, EYE_R * blinkScale * eyeScale, 1f);
        Matrix.multiplyMM(tmp2, 0, mHead, 0, out, 0);
        System.arraycopy(tmp2, 0, out, 0, 16);
    }

    private void drawMesh(Mesh m, float[] model, int mode, float[] tint, float alpha) {
        Matrix.multiplyMM(mvp, 0, pv, 0, model, 0);
        GLES30.glUniformMatrix4fv(uMVP, 1, false, mvp, 0);
        GLES30.glUniformMatrix4fv(uModel, 1, false, model, 0);
        GLES30.glUniform3fv(uTint, 1, tint, 0);
        GLES30.glUniform1f(uAlpha, alpha);
        GLES30.glUniform1i(uMode, mode);
        for (int i = 0; i < 3; i++) {
            GLES30.glBindBuffer(GLES30.GL_ARRAY_BUFFER, m.vbo[i]);
            GLES30.glVertexAttribPointer(i, i == 2 ? 4 : 3, GLES30.GL_FLOAT, false, 0, 0);
            GLES30.glEnableVertexAttribArray(i);
        }
        GLES30.glDrawArrays(GLES30.GL_TRIANGLES, 0, m.count);
    }

    // =============================================================== GL utils
    private int createProgram(String vs, String fs) {
        int v = compile(GLES30.GL_VERTEX_SHADER, vs);
        int f = compile(GLES30.GL_FRAGMENT_SHADER, fs);
        int p = GLES30.glCreateProgram();
        GLES30.glAttachShader(p, v);
        GLES30.glAttachShader(p, f);
        GLES30.glLinkProgram(p);
        int[] status = new int[1];
        GLES30.glGetProgramiv(p, GLES30.GL_LINK_STATUS, status, 0);
        if (status[0] == 0) {
            Log.e(TAG, "Link error: " + GLES30.glGetProgramInfoLog(p));
        }
        return p;
    }

    private int compile(int type, String src) {
        int s = GLES30.glCreateShader(type);
        GLES30.glShaderSource(s, src);
        GLES30.glCompileShader(s);
        int[] status = new int[1];
        GLES30.glGetShaderiv(s, GLES30.GL_COMPILE_STATUS, status, 0);
        if (status[0] == 0) {
            Log.e(TAG, "Shader compile error: " + GLES30.glGetShaderInfoLog(s));
        }
        return s;
    }

    private int createVAO() {
        int[] vao = new int[1];
        GLES30.glGenVertexArrays(1, vao, 0);
        return vao[0];
    }

    private void upload(Mesh m) {
        GLES30.glGenBuffers(3, m.vbo, 0);
        putVBO(m.vbo[0], m.pos);
        putVBO(m.vbo[1], m.nrm);
        putVBO(m.vbo[2], m.col);
    }

    private void putVBO(int id, float[] data) {
        FloatBuffer fb = ByteBuffer.allocateDirect(data.length * 4)
                .order(ByteOrder.nativeOrder()).asFloatBuffer();
        fb.put(data);
        // cast explícito: Android API 26 não tem o override covariante FloatBuffer.position(int)
        ((java.nio.Buffer) fb).position(0);
        GLES30.glBindBuffer(GLES30.GL_ARRAY_BUFFER, id);
        GLES30.glBufferData(GLES30.GL_ARRAY_BUFFER, data.length * 4, fb, GLES30.GL_STATIC_DRAW);
    }

    // =============================================================== builders
    private static final float[] TINT_WHITE = {1f, 1f, 1f};
    private static final float[] TINT_NECK = {0.30f, 0.31f, 0.34f};
    private static final float[] TINT_SOCKET = {0.05f, 0.055f, 0.07f};
    private static final float[] TINT_PINK = {1.00f, 0.34f, 0.66f};

    /** Buffers de crescimento simples. */
    private static class Buf {
        float[] a = new float[4096];
        int n;

        void add(float... v) {
            if (n + v.length > a.length) {
                float[] b = new float[a.length * 2];
                System.arraycopy(a, 0, b, 0, n);
                a = b;
            }
            System.arraycopy(v, 0, a, n, v.length);
            n += v.length;
        }

        float[] trim() {
            float[] b = new float[n];
            System.arraycopy(a, 0, b, 0, n);
            return b;
        }
    }

    private static void tri(Buf p, Buf nr, Buf c,
                            float[] p1, float[] p2, float[] p3,
                            float[] n1, float[] n2, float[] n3,
                            float[] c1, float[] c2, float[] c3) {
        p.add(p1[0], p1[1], p1[2], p2[0], p2[1], p2[2], p3[0], p3[1], p3[2]);
        nr.add(n1[0], n1[1], n1[2], n2[0], n2[1], n2[2], n3[0], n3[1], n3[2]);
        c.add(c1[0], c1[1], c1[2], 1f, c2[0], c2[1], c2[2], 1f, c3[0], c3[1], c3[2], 1f);
    }

    /**
     * Casca do capacete (elipsoide) estilo EVE: casca branca perolada lisa +
     * visor facial escuro convexo com gradiente suave e guarnição escura na
     * borda. Costuras apenas discretas na casca.
     */
    private Mesh buildHead() {
        final int SP = 96, ST = 64;
        Buf p = new Buf(), nr = new Buf(), c = new Buf();
        float dPhi = (float) (2.0 * Math.PI / SP);
        float phiC = (float) (Math.PI / 2.0); // frente (+z)

        float[] shell = {0.90f, 0.91f, 0.93f};
        float[] seam = {0.72f, 0.74f, 0.78f};

        for (int j = 0; j < ST; j++) {
            float t0 = (float) (Math.PI * j / ST);
            float t1 = (float) (Math.PI * (j + 1) / ST);
            for (int i = 0; i < SP; i++) {
                float f0 = dPhi * i;
                float f1 = dPhi * (i + 1);
                float[] v00 = sph(t0, f0), v10 = sph(t0, f1), v01 = sph(t1, f0), v11 = sph(t1, f1);
                float[] n00 = sphN(v00), n10 = sphN(v10), n01 = sphN(v01), n11 = sphN(v11);
                float[] cc00 = headColor(t0, f0, v00, dPhi, phiC, shell, seam);
                float[] cc10 = headColor(t0, f1, v10, dPhi, phiC, shell, seam);
                float[] cc01 = headColor(t1, f0, v01, dPhi, phiC, shell, seam);
                float[] cc11 = headColor(t1, f1, v11, dPhi, phiC, shell, seam);
                tri(p, nr, c, v00, v10, v11, n00, n10, n11, cc00, cc10, cc11);
                tri(p, nr, c, v00, v11, v01, n00, n11, n01, cc00, cc11, cc01);
            }
        }
        return new Mesh(p.trim(), nr.trim(), c.trim());
    }

    private float[] sph(float theta, float phi) {
        float st = (float) Math.sin(theta), ct = (float) Math.cos(theta);
        return new float[]{RX * st * (float) Math.cos(phi), RY * ct, RZ * st * (float) Math.sin(phi)};
    }

    private float[] sphN(float[] v) {
        float nx = v[0] / (RX * RX), ny = v[1] / (RY * RY), nz = v[2] / (RZ * RZ);
        float l = (float) Math.sqrt(nx * nx + ny * ny + nz * nz);
        return new float[]{nx / l, ny / l, nz / l};
    }

    private float[] headColor(float theta, float phi, float[] v, float dPhi, float phiC,
                              float[] shell, float[] seam) {
        float dPhiC = phi - phiC;
        while (dPhiC > Math.PI) dPhiC -= 2 * Math.PI;
        while (dPhiC < -Math.PI) dPhiC += 2 * Math.PI;

        // --- região do visor (face frontal convexa)
        boolean inMaskY = v[1] > -0.95f && v[1] < 0.72f;
        boolean isMask = Math.abs(dPhiC) < 1.05f && inMaskY && v[2] > 0f;
        if (isMask) {
            // guarnição escura na borda do visor (vedação do vidro)
            float edge = 1.05f - Math.abs(dPhiC);
            float edgeY = Math.min(0.72f - v[1], v[1] + 0.95f);
            if (edge < 0.055f || edgeY < 0.055f) {
                return new float[]{0.035f, 0.040f, 0.052f};
            }
            // vidro escuro com gradiente suave (mais claro no topo = reflexo)
            float g = Math.max(0f, Math.min(1f, (v[1] + 0.95f) / 1.67f));
            float top0 = 0.062f, top1 = 0.076f, top2 = 0.096f;
            float bot0 = 0.026f, bot1 = 0.032f, bot2 = 0.045f;
            return new float[]{
                    bot0 + (top0 - bot0) * g,
                    bot1 + (top1 - bot1) * g,
                    bot2 + (top2 - bot2) * g};
        }

        // --- casca: costuras discretas
        float sinT = (float) Math.max(0.15, Math.sin(theta));
        float dY = RY * ((float) Math.PI / 64f) * sinT;
        boolean seamRing = (Math.abs(v[1] - 0.60f) < dY * 0.5f && Math.abs(dPhiC) > 1.05f)
                || (Math.abs(v[1] + 0.80f) < dY * 0.5f && Math.abs(dPhiC) > 1.05f);
        if (seamRing) return seam;
        return shell;
    }

    /** Pescoço-cilindro escuro (não acompanha a rotação da cabeça). */
    private Mesh buildNeck() {
        Buf p = new Buf(), nr = new Buf(), c = new Buf();
        float r = 0.46f, y0 = -1.62f, y1 = -1.05f, yc = -1.32f;
        int seg = 48;
        float[] col = {0.26f, 0.27f, 0.30f};
        for (int i = 0; i < seg; i++) {
            float a0 = (float) (2 * Math.PI * i / seg), a1 = (float) (2 * Math.PI * (i + 1) / seg);
            float[] q00 = {r * (float) Math.cos(a0), y0 - yc, r * (float) Math.sin(a0)};
            float[] q10 = {r * (float) Math.cos(a1), y0 - yc, r * (float) Math.sin(a1)};
            float[] q01 = {r * (float) Math.cos(a0), y1 - yc, r * (float) Math.sin(a0)};
            float[] q11 = {r * (float) Math.cos(a1), y1 - yc, r * (float) Math.sin(a1)};
            float[] n00 = {(float) Math.cos(a0), 0, (float) Math.sin(a0)};
            float[] n10 = {(float) Math.cos(a1), 0, (float) Math.sin(a1)};
            tri(p, nr, c, q00, q10, q11, n00, n10, n10, col, col, col);
            tri(p, nr, c, q00, q11, q01, n00, n10, n00, col, col, col);
            float[] up = {0f, -1f, 0f};
            tri(p, nr, c, new float[]{0, y0 - yc, 0}, q10, q00, up, up, up, col, col, col);
        }
        Mesh m = new Mesh(p.trim(), nr.trim(), c.trim());
        for (int i = 1; i < m.pos.length; i += 3) m.pos[i] += yc;
        return m;
    }

    /** Orelhas-cilindro (eixos X) nos dois lados — casca branca. */
    private Mesh buildEars() {
        Buf p = new Buf(), nr = new Buf(), c = new Buf();
        int seg = 48;
        float r = 0.21f, half = 0.07f, yc = 0.02f;
        float[] side = {0.86f, 0.87f, 0.90f};
        float[] cap = {0.10f, 0.105f, 0.12f};
        for (int s = 0; s < 2; s++) {
            float sign = (s == 0) ? -1f : 1f;
            float xc = sign * (RX + 0.02f);
            for (int i = 0; i < seg; i++) {
                float a0 = (float) (2 * Math.PI * i / seg), a1 = (float) (2 * Math.PI * (i + 1) / seg);
                float y0 = yc + r * (float) Math.sin(a0), z0 = r * (float) Math.cos(a0);
                float y1 = yc + r * (float) Math.sin(a1), z1 = r * (float) Math.cos(a1);
                float[] q00 = {xc - sign * half, y0, z0};
                float[] q10 = {xc - sign * half, y1, z1};
                float[] q01 = {xc + sign * half, y0, z0};
                float[] q11 = {xc + sign * half, y1, z1};
                float[] n0 = {0f, (float) Math.sin(a0), (float) Math.cos(a0)};
                float[] n1 = {0f, (float) Math.sin(a1), (float) Math.cos(a1)};
                tri(p, nr, c, q00, q10, q11, n0, n1, n1, side, side, side);
                tri(p, nr, c, q00, q11, q01, n0, n1, n0, side, side, side);
                float[] nc = {sign, 0f, 0f};
                float[] ctr = {xc + sign * half, yc, 0f};
                tri(p, nr, c, ctr, q10, q00, nc, nc, nc, cap, cap, cap);
            }
        }
        return new Mesh(p.trim(), nr.trim(), c.trim());
    }

    /** Anéis emissivos nas tampas das orelhas (modo aditivo). */
    private Mesh buildEarGlow() {
        Buf p = new Buf(), nr = new Buf(), c = new Buf();
        int seg = 48;
        float r0 = 0.075f, r1 = 0.135f, yc = 0.02f;
        for (int s = 0; s < 2; s++) {
            float sign = (s == 0) ? -1f : 1f;
            float xc = sign * (RX + 0.02f + halfEar() + 0.004f);
            float[] nc = {sign, 0f, 0f};
            for (int i = 0; i < seg; i++) {
                float a0 = (float) (2 * Math.PI * i / seg), a1 = (float) (2 * Math.PI * (i + 1) / seg);
                float[] i0 = {xc, yc + r0 * (float) Math.sin(a0), r0 * (float) Math.cos(a0)};
                float[] i1 = {xc, yc + r0 * (float) Math.sin(a1), r0 * (float) Math.cos(a1)};
                float[] o0 = {xc, yc + r1 * (float) Math.sin(a0), r1 * (float) Math.cos(a0)};
                float[] o1 = {xc, yc + r1 * (float) Math.sin(a1), r1 * (float) Math.cos(a1)};
                float[] w = {1f, 1f, 1f};
                tri(p, nr, c, i0, o0, o1, nc, nc, nc, w, w, w);
                tri(p, nr, c, i0, o1, i1, nc, nc, nc, w, w, w);
            }
        }
        return new Mesh(p.trim(), nr.trim(), c.trim());
    }

    private static float halfEar() { return 0.07f; }

    /** Soquetos escuros dos olhos, orientados pela normal do visor. */
    private Mesh buildSockets() {
        Buf p = new Buf(), nr = new Buf(), c = new Buf();
        int seg = 56;
        float[] col = {1f, 1f, 1f};
        for (int e = 0; e < 2; e++) {
            float sx = (e == 0 ? -EYE_X : EYE_X);
            float sz = eyeZ() + 0.002f;
            float a = RX * MASK, b = RY * MASK, cc = RZ * MASK;
            float nx = sx / (a * a), ny = EYE_Y / (b * b), nz = sz / (cc * cc);
            float l = (float) Math.sqrt(nx * nx + ny * ny + nz * nz);
            nx /= l; ny /= l; nz /= l;
            float tx = nz, ty = 0f, tz = -nx;
            float tl = (float) Math.sqrt(tx * tx + tz * tz);
            tx /= tl; tz /= tl;
            float bx = ny * tz - nz * ty, by = nz * tx - nx * tz, bz = nx * ty - ny * tx;
            float[] n = {nx, ny, nz};
            float R = EYE_R * 1.12f;
            float[] ctr = {sx, EYE_Y, sz};
            for (int i = 0; i < seg; i++) {
                float a0 = (float) (2 * Math.PI * i / seg), a1 = (float) (2 * Math.PI * (i + 1) / seg);
                float[] v0 = pt(ctr, tx, ty, tz, bx, by, bz, R * (float) Math.cos(a0), R * (float) Math.sin(a0));
                float[] v1 = pt(ctr, tx, ty, tz, bx, by, bz, R * (float) Math.cos(a1), R * (float) Math.sin(a1));
                tri(p, nr, c, ctr, v1, v0, n, n, n, col, col, col);
            }
        }
        return new Mesh(p.trim(), nr.trim(), c.trim());
    }

    private static float[] pt(float[] o, float tx, float ty, float tz, float bx, float by, float bz, float u, float v) {
        return new float[]{o[0] + tx * u + bx * v, o[1] + ty * u + by * v, o[2] + tz * u + bz * v};
    }

    /** Anel (discos vazados) no plano XY, normal +z, cor = fator de brilho. */
    private Mesh buildAnnulus(float r0, float r1, int seg, float bright) {
        Buf p = new Buf(), nr = new Buf(), c = new Buf();
        float[] n = {0f, 0f, 1f};
        float[] col = {bright, bright, bright};
        for (int i = 0; i < seg; i++) {
            float a0 = (float) (2 * Math.PI * i / seg), a1 = (float) (2 * Math.PI * (i + 1) / seg);
            float[] i0 = {r0 * (float) Math.cos(a0), r0 * (float) Math.sin(a0), 0f};
            float[] i1 = {r0 * (float) Math.cos(a1), r0 * (float) Math.sin(a1), 0f};
            float[] o0 = {r1 * (float) Math.cos(a0), r1 * (float) Math.sin(a0), 0f};
            float[] o1 = {r1 * (float) Math.cos(a1), r1 * (float) Math.sin(a1), 0f};
            tri(p, nr, c, i0, o0, o1, n, n, n, col, col, col);
            tri(p, nr, c, i0, o1, i1, n, n, n, col, col, col);
        }
        return new Mesh(p.trim(), nr.trim(), c.trim());
    }

    /** Disco cheio (pupila/aura) no plano XY, normal +z. */
    private Mesh buildDisc(float r, int seg, float bright) {
        Buf p = new Buf(), nr = new Buf(), c = new Buf();
        float[] n = {0f, 0f, 1f};
        float[] col = {bright, bright, bright};
        float[] ctr = {0f, 0f, 0f};
        for (int i = 0; i < seg; i++) {
            float a0 = (float) (2 * Math.PI * i / seg), a1 = (float) (2 * Math.PI * (i + 1) / seg);
            float[] v0 = {r * (float) Math.cos(a0), r * (float) Math.sin(a0), 0f};
            float[] v1 = {r * (float) Math.cos(a1), r * (float) Math.sin(a1), 0f};
            tri(p, nr, c, ctr, v1, v0, n, n, n, col, col, col);
        }
        return new Mesh(p.trim(), nr.trim(), c.trim());
    }

    /** Halo de bloom: 3 anéis aditivos com intensidade decrescente p/ fora. */
    private Mesh buildHalo() {
        Buf p = new Buf(), nr = new Buf(), c = new Buf();
        float[] n = {0f, 0f, 1f};
        int seg = 56;
        float[][] bands = {{0.30f, 0.58f, 0.20f}, {0.58f, 0.80f, 0.12f}, {0.80f, 1.00f, 0.07f}};
        for (float[] band : bands) {
            float r0 = band[0], r1 = band[1], f = band[2];
            float[] col = {f, f, f};
            for (int i = 0; i < seg; i++) {
                float a0 = (float) (2 * Math.PI * i / seg), a1 = (float) (2 * Math.PI * (i + 1) / seg);
                float[] i0 = {r0 * (float) Math.cos(a0), r0 * (float) Math.sin(a0), 0f};
                float[] i1 = {r0 * (float) Math.cos(a1), r0 * (float) Math.sin(a1), 0f};
                float[] o0 = {r1 * (float) Math.cos(a0), r1 * (float) Math.sin(a0), 0f};
                float[] o1 = {r1 * (float) Math.cos(a1), r1 * (float) Math.sin(a1), 0f};
                tri(p, nr, c, i0, o0, o1, n, n, n, col, col, col);
                tri(p, nr, c, i0, o1, i1, n, n, n, col, col, col);
            }
        }
        return new Mesh(p.trim(), nr.trim(), c.trim());
    }

    /** Caixa unitária centrada na origem, normais por face. */
    private Mesh buildBox() {
        Buf p = new Buf(), nr = new Buf(), c = new Buf();
        float[] col = {1f, 1f, 1f};
        float h = 0.5f;
        float[][][] faces = {
                {{-h, -h, h}, {h, -h, h}, {h, h, h}, {-h, h, h}},
                {{h, -h, -h}, {-h, -h, -h}, {-h, h, -h}, {h, h, -h}},
                {{h, -h, h}, {h, -h, -h}, {h, h, -h}, {h, h, h}},
                {{-h, -h, -h}, {-h, -h, h}, {-h, h, h}, {-h, h, -h}},
                {{-h, h, h}, {h, h, h}, {h, h, -h}, {-h, h, -h}},
                {{-h, -h, -h}, {h, -h, -h}, {h, -h, h}, {-h, -h, h}}
        };
        float[][] norms = {{0, 0, 1}, {0, 0, -1}, {1, 0, 0}, {-1, 0, 0}, {0, 1, 0}, {0, -1, 0}};
        for (int f = 0; f < 6; f++) {
            float[] v0 = faces[f][0], v1 = faces[f][1], v2 = faces[f][2], v3 = faces[f][3];
            float[] nn = norms[f];
            tri(p, nr, c, v0, v1, v2, nn, nn, nn, col, col, col);
            tri(p, nr, c, v0, v2, v3, nn, nn, nn, col, col, col);
        }
        return new Mesh(p.trim(), nr.trim(), c.trim());
    }

    // ====================================================== corpo chibi (v3)
    /** Só as 5 poses de corpo da linha 4 do sheet mostram o busto. */
    private static boolean isBodyState(String s) {
        return "saudacao_completa".equals(s) || "hud".equals(s)
                || "processando".equals(s) || "comemoracao".equals(s)
                || "escuta_ativa".equals(s);
    }

    /** Ombros/torso + braços articulados + mãos brancos (estados de corpo). */
    private void drawBody() {
        Matrix.setIdentityM(mBody, 0);
        Matrix.scaleM(mBody, 0, MODEL_SCALE, MODEL_SCALE, MODEL_SCALE);
        float breath = (float) Math.sin(animT * 1.45f) * 0.014f;
        Matrix.translateM(mBody, 0, 0f, breath * 0.55f, 0f);

        drawMesh(torsoMesh, mBody, 0, TINT_WHITE, 1f);
        drawArm(+1f, armLR, armFR, armER);
        drawArm(-1f, armLL, armFL, armEL);

        // anéis LED laterais no busto (emissivos — desenhados no passe aditivo
        // via drawExtras quando bodyAmt > 0; aqui só o geometry pass opaco)
    }

    private void drawArm(float side, float lift, float fwd, float elbow) {
        float sx = side * 1.02f, sy = -2.00f, sz = 0.06f;
        float liftS = side > 0 ? lift : -lift;

        Matrix.setIdentityM(mArm, 0);
        Matrix.translateM(mArm, 0, sx, sy, sz);
        Matrix.rotateM(mArm, 0, (float) Math.toDegrees(liftS), 0f, 0f, 1f);
        Matrix.rotateM(mArm, 0, (float) Math.toDegrees(fwd), 1f, 0f, 0f);

        // braço superior (ombro -> cotovelo)
        Matrix.multiplyMM(tmp, 0, mBody, 0, mArm, 0);
        drawMesh(upperArmMesh, tmp, 0, TINT_WHITE, 1f);

        // antebraço no cotovelo
        Matrix.setIdentityM(mFore, 0);
        Matrix.translateM(mFore, 0, 0f, -0.54f, 0f);
        Matrix.rotateM(mFore, 0, (float) Math.toDegrees(elbow), 1f, 0f, 0f);
        Matrix.multiplyMM(mHand, 0, mArm, 0, mFore, 0);
        Matrix.multiplyMM(tmp, 0, mBody, 0, mHand, 0);
        drawMesh(foreArmMesh, tmp, 0, TINT_WHITE, 1f);

        // mão no punho
        Matrix.setIdentityM(mFore, 0);
        Matrix.translateM(mFore, 0, 0f, -0.50f, 0f);
        Matrix.multiplyMM(tmp2, 0, mHand, 0, mFore, 0);
        drawMesh(handMesh, tmp2, 0, TINT_WHITE, 1f);
    }

    /** Extras animados: Zzz, ?, lâmpada, hologramas, confete, onda, LEDs. */
    private void drawExtras() {
        // Zzz (SONOLENTO)
        if (zzzAmt > 0.01f) {
            for (int zi = 0; zi < 3; zi++) {
                float ph = (animT * 0.30f + zi * 0.33f) % 1f;
                float zs = 0.20f + zi * 0.09f;
                float zx = 0.82f + ph * 0.30f + zi * 0.13f;
                float zy = 0.70f + ph * 0.75f + zi * 0.26f;
                float fa = (ph < 0.15f ? ph / 0.15f : 1f) * (1f - ph * 0.7f) * zzzAmt;
                if (fa <= 0.02f) continue;
                Matrix.setIdentityM(mPart, 0);
                Matrix.translateM(mPart, 0, zx, zy, 1.35f);
                Matrix.rotateM(mPart, 0, (float) Math.sin(animT + zi) * 6f, 0f, 0f, 1f);
                Matrix.scaleM(mPart, 0, zs, zs, zs);
                drawMesh(zMesh, mPart, 1, eyeTint(1.15f), fa * 0.95f);
            }
        }

        // "?" flutuante (CONFUSO)
        if (questAmt > 0.01f) {
            float bob = (float) Math.sin(animT * 2.4f) * 0.07f;
            float qa = questAmt * (0.85f + 0.15f * (float) Math.sin(animT * 3.3f));
            Matrix.setIdentityM(mPart, 0);
            Matrix.translateM(mPart, 0, 1.18f, 0.92f + bob, 1.25f);
            Matrix.rotateM(mPart, 0, (float) Math.sin(animT * 1.7f) * 8f, 0f, 0f, 1f);
            Matrix.scaleM(mPart, 0, 0.55f, 0.55f, 0.55f);
            drawMesh(questionMesh, mPart, 1, eyeTint(1.2f), qa);
        }

        // lâmpada (PROCESSANDO)
        if (bulbAmt > 0.01f) {
            float bob = (float) Math.sin(animT * 2.0f) * 0.06f;
            float ba = bulbAmt * (0.8f + 0.2f * (float) Math.sin(animT * 5.5f));
            Matrix.setIdentityM(mPart, 0);
            Matrix.translateM(mPart, 0, 0.55f, 1.75f + bob, 0.9f);
            Matrix.scaleM(mPart, 0, 0.42f, 0.42f, 0.42f);
            drawMesh(bulbMesh, mPart, 1, eyeTint(1.25f), ba);
        }

        // painéis holográficos (HUD)
        if (holoAmt > 0.01f) {
            for (int pi = 0; pi < 3; pi++) {
                float px = (pi == 0 ? -1.30f : (pi == 1 ? 1.30f : 0f));
                float py = (pi == 2 ? 1.70f : -0.35f)
                        + (float) Math.sin(animT * 1.6f + pi) * 0.06f;
                float pz = (pi == 2 ? 0.8f : 1.05f);
                float rot = (pi == 0 ? 20f : (pi == 1 ? -20f : 0f));
                float ha = holoAmt * (0.30f + 0.10f
                        * (float) Math.sin(animT * 3.0f + pi * 2f));
                Matrix.setIdentityM(mPart, 0);
                Matrix.translateM(mPart, 0, px, py, pz);
                Matrix.rotateM(mPart, 0, rot, 0f, 1f, 0f);
                Matrix.scaleM(mPart, 0, 0.78f, 0.58f, 0.02f);
                drawMesh(panelMesh, mPart, 1, eyeTint(1f), ha);
                // barras de dados dentro do painel
                for (int bi = 0; bi < 3; bi++) {
                    float frac = 0.35f + 0.55f * (0.5f + 0.5f
                            * (float) Math.sin(animT * 2.6f + bi * 1.7f + pi));
                    Matrix.setIdentityM(tmp, 0);
                    Matrix.translateM(tmp, 0, -0.35f + frac * 0.35f, 0.22f - bi * 0.20f, 0.04f);
                    Matrix.scaleM(tmp, 0, frac * 1.2f, 0.10f, 1f);
                    Matrix.multiplyMM(tmp2, 0, mPart, 0, tmp, 0);
                    drawMesh(barMesh, tmp2, 1, eyeTint(1.3f), ha * 1.8f);
                }
            }
        }

        // confete (COMEMORAÇÃO)
        if (confettiAmt > 0.01f) {
            for (int ci = 0; ci < 24; ci++) {
                float rx = frnd(ci, 1f), ry = frnd(ci, 2f);
                float rz = frnd(ci, 3f), rr = frnd(ci, 4f);
                float period = 2.6f + rz * 1.4f;
                float ph = (animT * (0.9f / period) + ry) % 1f;
                float cx = (rx - 0.5f) * 3.4f;
                float cy = 2.4f - ph * 5.2f;
                float cz = -0.6f + rz * 2.2f;
                float spin = animT * (140f + rr * 220f) * (rr > 0.5f ? 1f : -1f);
                float a = confettiAmt;
                if (ph < 0.05f) a *= ph / 0.05f;
                if (ph > 0.9f) a *= (1f - ph) / 0.1f;
                if (a <= 0.02f) continue;
                float[] tint = (ci % 3 == 0) ? TINT_PINK
                        : ((ci % 3 == 1) ? eyeTint(1f) : new float[]{1f, 1f, 1f});
                Matrix.setIdentityM(mPart, 0);
                Matrix.translateM(mPart, 0, cx, cy, cz);
                Matrix.rotateM(mPart, 0, spin, 0.4f, 1f, 0.2f);
                Matrix.scaleM(mPart, 0, 0.10f, 0.16f, 0.02f);
                drawMesh(barMesh, mPart, 1, tint, a * 0.95f);
            }
        }

        // onda de áudio (MODO ESCUTA ATIVA)
        if (waveAmt > 0.01f) {
            for (int wi = 0; wi < 9; wi++) {
                float ampw = 0.5f + 0.5f
                        * (float) Math.sin(animT * 6.5f - wi * 0.7f);
                float hgt = 0.12f + ampw * 0.55f;
                float wx = 1.05f + wi * 0.09f;
                float a = waveAmt * (0.55f + 0.45f * ampw);
                Matrix.setIdentityM(mPart, 0);
                Matrix.translateM(mPart, 0, wx, -0.45f, 1.1f);
                Matrix.scaleM(mPart, 0, 0.055f, hgt, 0.03f);
                drawMesh(barMesh, mPart, 1, eyeTint(1.2f), a);
            }
        }

        // anéis LED laterais do busto (estados de corpo)
        if (bodyAmt > 0.01f) {
            float la = bodyAmt * (0.55f + 0.25f * (float) Math.sin(animT * 2.8f));
            if ("escuta_ativa".equals(currentExpr) || "escutando".equals(currentExpr)) {
                la = bodyAmt * (0.75f + 0.25f * (float) Math.sin(animT * 5.0f));
            }
            for (int s = 0; s < 2; s++) {
                float lx = (s == 0 ? -1.05f : 1.05f);
                Matrix.setIdentityM(mPart, 0);
                Matrix.translateM(mPart, 0, lx, -1.98f, 0.45f);
                Matrix.rotateM(mPart, 0, (s == 0 ? 14f : -14f), 0f, 1f, 0f);
                drawMesh(ledRingMesh, mPart, 1, eyeTint(1.1f), la);
            }
        }
    }

    /** Hash determinístico [0,1) para partículas (sem Random por frame). */
    private static float frnd(int i, float salt) {
        float x = (i + 1) * 12.9898f + salt * 78.233f;
        float s = (float) Math.sin(x) * 43758.5453f;
        return s - (float) Math.floor(s);
    }

    // ---------------------------------------------------- builders do corpo
    /** Casca do busto: elipsoide do peito + esferas de ombro (branco chibi). */
    private Mesh buildTorso() {
        Buf p = new Buf(), nr = new Buf(), c = new Buf();
        float[] col = {0.90f, 0.91f, 0.93f};
        sphereInto(p, nr, c, new float[]{0f, -2.32f, 0f},
                new float[]{0.95f, 0.68f, 0.55f}, 48, 32, col);
        sphereInto(p, nr, c, new float[]{-0.80f, -1.98f, 0f},
                new float[]{0.34f, 0.32f, 0.34f}, 24, 16, col);
        sphereInto(p, nr, c, new float[]{0.80f, -1.98f, 0f},
                new float[]{0.34f, 0.32f, 0.34f}, 24, 16, col);
        return new Mesh(p.trim(), nr.trim(), c.trim());
    }

    /** Elipsoide genérica (busto/ombros) com normais de gradiente. */
    private static void sphereInto(Buf p, Buf nr, Buf c,
                                   float[] ctr, float[] rad, int sp, int st,
                                   float[] col) {
        for (int j = 0; j < st; j++) {
            double t0 = Math.PI * j / st, t1 = Math.PI * (j + 1) / st;
            for (int i = 0; i < sp; i++) {
                double f0 = 2.0 * Math.PI * i / sp;
                double f1 = 2.0 * Math.PI * (i + 1) / sp;
                float[] v00 = ell(ctr, rad, t0, f0), v10 = ell(ctr, rad, t0, f1);
                float[] v01 = ell(ctr, rad, t1, f0), v11 = ell(ctr, rad, t1, f1);
                float[] n00 = ellN(ctr, rad, t0, f0), n10 = ellN(ctr, rad, t0, f1);
                float[] n01 = ellN(ctr, rad, t1, f0), n11 = ellN(ctr, rad, t1, f1);
                tri(p, nr, c, v00, v10, v11, n00, n10, n11, col, col, col);
                tri(p, nr, c, v00, v11, v01, n00, n11, n01, col, col, col);
            }
        }
    }

    private static float[] ell(float[] ctr, float[] rad, double th, double ph) {
        float st = (float) Math.sin(th), ct = (float) Math.cos(th);
        return new float[]{
                ctr[0] + rad[0] * st * (float) Math.cos(ph),
                ctr[1] + rad[1] * ct,
                ctr[2] + rad[2] * st * (float) Math.sin(ph)};
    }

    private static float[] ellN(float[] ctr, float[] rad, double th, double ph) {
        float st = (float) Math.sin(th), ct = (float) Math.cos(th);
        float lx = (float) (rad[0] * st * Math.cos(ph)) / (rad[0] * rad[0]);
        float ly = (float) (rad[1] * ct) / (rad[1] * rad[1]);
        float lz = (float) (rad[2] * st * Math.sin(ph)) / (rad[2] * rad[2]);
        float l = (float) Math.sqrt(lx * lx + ly * ly + lz * lz);
        if (l < 1e-5f) return new float[]{0f, 1f, 0f};
        return new float[]{lx / l, ly / l, lz / l};
    }

    /** Cápsula: cilindro de y=0 ate y=-len + extremidades esfericas. */
    private Mesh buildCapsule(float r, float len) {
        Buf p = new Buf(), nr = new Buf(), c = new Buf();
        float[] col = {1f, 1f, 1f};
        int seg = 24, rings = 6;
        // lado
        for (int i = 0; i < seg; i++) {
            float a0 = (float) (2 * Math.PI * i / seg);
            float a1 = (float) (2 * Math.PI * (i + 1) / seg);
            float[] n0 = {(float) Math.cos(a0), 0f, (float) Math.sin(a0)};
            float[] n1 = {(float) Math.cos(a1), 0f, (float) Math.sin(a1)};
            float[] q00 = {r * (float) Math.cos(a0), 0f, r * (float) Math.sin(a0)};
            float[] q10 = {r * (float) Math.cos(a1), 0f, r * (float) Math.sin(a1)};
            float[] q01 = {r * (float) Math.cos(a0), -len, r * (float) Math.sin(a0)};
            float[] q11 = {r * (float) Math.cos(a1), -len, r * (float) Math.sin(a1)};
            tri(p, nr, c, q00, q10, q11, n0, n1, n1, col, col, col);
            tri(p, nr, c, q00, q11, q01, n0, n1, n0, col, col, col);
        }
        capHemi(p, nr, c, r, 0f, false, seg, rings, col);      // topo
        capHemi(p, nr, c, r, -len, true, seg, rings, col);     // base
        return new Mesh(p.trim(), nr.trim(), c.trim());
    }

    private static void capHemi(Buf p, Buf nr, Buf c, float r, float cy,
                                boolean down, int seg, int rings, float[] col) {
        for (int j = 0; j < rings; j++) {
            double th0 = (Math.PI / 2.0) * j / rings;
            double th1 = (Math.PI / 2.0) * (j + 1) / rings;
            for (int i = 0; i < seg; i++) {
                double a0 = 2 * Math.PI * i / seg;
                double a1 = 2 * Math.PI * (i + 1) / seg;
                float[] v00 = capPt(r, cy, th0, a0, down);
                float[] v10 = capPt(r, cy, th0, a1, down);
                float[] v01 = capPt(r, cy, th1, a0, down);
                float[] v11 = capPt(r, cy, th1, a1, down);
                float[] n00 = capN(th0, a0, down);
                float[] n10 = capN(th0, a1, down);
                float[] n01 = capN(th1, a0, down);
                float[] n11 = capN(th1, a1, down);
                tri(p, nr, c, v00, v10, v11, n00, n10, n11, col, col, col);
                tri(p, nr, c, v00, v11, v01, n00, n11, n01, col, col, col);
            }
        }
    }

    private static float[] capPt(float r, float cy, double th, double ph, boolean down) {
        float rad = r * (float) Math.cos(th);
        float dy = r * (float) Math.sin(th);
        return new float[]{rad * (float) Math.cos(ph),
                down ? cy - dy : cy + dy,
                rad * (float) Math.sin(ph)};
    }

    private static float[] capN(double th, double ph, boolean down) {
        float ct = (float) Math.cos(th), st = (float) Math.sin(th);
        return new float[]{ct * (float) Math.cos(ph),
                down ? -st : st,
                ct * (float) Math.sin(ph)};
    }

    /** Mao chibi: esfera + polegar pequeno. */
    private Mesh buildHand() {
        Buf p = new Buf(), nr = new Buf(), c = new Buf();
        float[] col = {0.92f, 0.93f, 0.95f};
        sphereInto(p, nr, c, new float[]{0f, -0.06f, 0f},
                new float[]{0.17f, 0.17f, 0.16f}, 20, 14, col);
        sphereInto(p, nr, c, new float[]{0.11f, 0.04f, 0.05f},
                new float[]{0.07f, 0.09f, 0.07f}, 12, 8, col);
        return new Mesh(p.trim(), nr.trim(), c.trim());
    }

    /** Estrela de 5 pontas (olhos do EMPOLGADO). */
    private Mesh buildStar(float rOut, float rIn) {
        Buf p = new Buf(), nr = new Buf(), c = new Buf();
        float[] n = {0f, 0f, 1f};
        float[] col = {1f, 1f, 1f};
        float[] ctr = {0f, 0f, 0f};
        int pts = 10;
        float[] xs = new float[pts];
        float[] ys = new float[pts];
        for (int i = 0; i < pts; i++) {
            double a = Math.PI / 2.0 + i * Math.PI / 5.0;
            float r = (i % 2 == 0) ? rOut : rIn;
            xs[i] = r * (float) Math.cos(a);
            ys[i] = r * (float) Math.sin(a);
        }
        for (int i = 0; i < pts; i++) {
            int j = (i + 1) % pts;
            float[] v0 = {xs[i], ys[i], 0f};
            float[] v1 = {xs[j], ys[j], 0f};
            tri(p, nr, c, ctr, v1, v0, n, n, n, col, col, col);
        }
        return new Mesh(p.trim(), nr.trim(), c.trim());
    }

    /** Letra "Z" 3D (SONOLENTO) a partir de 3 caixas. */
    private Mesh buildZ() {
        Buf p = new Buf(), nr = new Buf(), c = new Buf();
        float[] col = {1f, 1f, 1f};
        boxInto(p, nr, c, new float[]{0f, 0.40f, 0f}, 0.40f, 0.07f, 0.07f, 0f, col);
        boxInto(p, nr, c, new float[]{0f, -0.40f, 0f}, 0.40f, 0.07f, 0.07f, 0f, col);
        boxInto(p, nr, c, new float[]{0f, 0f, 0f}, 0.07f, 0.44f, 0.07f, -32f, col);
        return new Mesh(p.trim(), nr.trim(), c.trim());
    }

    /** Painel holografico fino (HUD). */
    private Mesh buildPanel() {
        Buf p = new Buf(), nr = new Buf(), c = new Buf();
        float[] col = {1f, 1f, 1f};
        boxInto(p, nr, c, new float[]{0f, 0f, 0f}, 0.5f, 0.35f, 0.01f, 0f, col);
        return new Mesh(p.trim(), nr.trim(), c.trim());
    }

    /** Interrogacao "?" (CONFUSO): arco + haste + ponto. */
    private Mesh buildQuestion() {
        Buf p = new Buf(), nr = new Buf(), c = new Buf();
        float[] col = {1f, 1f, 1f};
        for (int i = 0; i < 8; i++) {
            double a = Math.toRadians(140.0 - i * (190.0 / 7.0));
            float cx = 0.40f * (float) Math.cos(a);
            float cy = 0.40f * (float) Math.sin(a) + 0.22f;
            float rot = (float) Math.toDegrees(a) + 90f;
            boxInto(p, nr, c, new float[]{cx, cy, 0f}, 0.09f, 0.07f, 0.07f, rot, col);
        }
        boxInto(p, nr, c, new float[]{0f, -0.30f, 0f}, 0.07f, 0.16f, 0.07f, 0f, col);
        boxInto(p, nr, c, new float[]{0f, -0.62f, 0f}, 0.09f, 0.09f, 0.07f, 0f, col);
        return new Mesh(p.trim(), nr.trim(), c.trim());
    }

    /** Lamada (PROCESSANDO): bulbo + soquete + raios. */
    private Mesh buildBulb() {
        Buf p = new Buf(), nr = new Buf(), c = new Buf();
        float[] glass = {1f, 1f, 1f};
        float[] base = {0.75f, 0.78f, 0.82f};
        sphereInto(p, nr, c, new float[]{0f, 0.08f, 0f},
                new float[]{0.34f, 0.36f, 0.34f}, 24, 16, glass);
        boxInto(p, nr, c, new float[]{0f, -0.32f, 0f}, 0.15f, 0.10f, 0.15f, 0f, base);
        for (int i = 0; i < 6; i++) {
            float ang = i * 60f;
            float rx = 0.55f * (float) Math.cos(Math.toRadians(ang));
            float ry = 0.55f * (float) Math.sin(Math.toRadians(ang)) + 0.08f;
            boxInto(p, nr, c, new float[]{rx, ry, 0f}, 0.14f, 0.025f, 0.025f, ang, glass);
        }
        return new Mesh(p.trim(), nr.trim(), c.trim());
    }

    /** Caixa centrada em ctr com meio-eixos (hx,hy,hz), rotacionada em Z. */
    private static void boxInto(Buf p, Buf nr, Buf c,
                                float[] ctr, float hx, float hy, float hz,
                                float rotZDeg, float[] col) {
        float rad = (float) Math.toRadians(rotZDeg);
        float cs = (float) Math.cos(rad), sn = (float) Math.sin(rad);
        // 8 cantos locais
        float[][] loc = {
                {-hx, -hy, -hz}, {hx, -hy, -hz}, {hx, hy, -hz}, {-hx, hy, -hz},
                {-hx, -hy, hz}, {hx, -hy, hz}, {hx, hy, hz}, {-hx, hy, hz}
        };
        float[][] v = new float[8][3];
        for (int i = 0; i < 8; i++) {
            float x = loc[i][0], y = loc[i][1];
            v[i][0] = ctr[0] + x * cs - y * sn;
            v[i][1] = ctr[1] + x * sn + y * cs;
            v[i][2] = ctr[2] + loc[i][2];
        }
        float[][] nloc = {
                {0, 0, -1}, {0, 0, 1}, {1, 0, 0}, {-1, 0, 0}, {0, 1, 0}, {0, -1, 0}
        };
        int[][] faces = {
                {0, 1, 2, 3}, {4, 7, 6, 5}, {1, 5, 6, 2},
                {0, 3, 7, 4}, {3, 2, 6, 7}, {0, 4, 5, 1}
        };
        for (int f = 0; f < 6; f++) {
            float lx = nloc[f][0], ly = nloc[f][1];
            float nx = lx * cs - ly * sn;
            float ny = lx * sn + ly * cs;
            float nz = nloc[f][2];
            float[] nn = {nx, ny, nz};
            float[] a = v[faces[f][0]], b = v[faces[f][1]];
            float[] cc = v[faces[f][2]], d = v[faces[f][3]];
            tri(p, nr, c, a, b, cc, nn, nn, nn, col, col, col);
            tri(p, nr, c, a, cc, d, nn, nn, nn, col, col, col);
        }
    }

    /** Coração (curva paramétrica clássica) para o estado CARINHOSO. */
    private Mesh buildHeart() {
        Buf p = new Buf(), nr = new Buf(), c = new Buf();
        float[] n = {0f, 0f, 1f};
        float[] col = {1f, 1f, 1f};
        float[] ctr = {0f, 0f, 0f};
        int seg = 48;
        float[] xs = new float[seg];
        float[] ys = new float[seg];
        for (int i = 0; i < seg; i++) {
            double t = 2.0 * Math.PI * i / seg;
            double x = 16.0 * Math.pow(Math.sin(t), 3.0);
            double y = 13.0 * Math.cos(t) - 5.0 * Math.cos(2 * t)
                    - 2.0 * Math.cos(3 * t) - Math.cos(4 * t);
            xs[i] = (float) (x / 17.0);
            ys[i] = (float) ((y + 4.0) / 17.0);
        }
        for (int i = 0; i < seg; i++) {
            int j = (i + 1) % seg;
            float[] v0 = {xs[i], ys[i], 0f};
            float[] v1 = {xs[j], ys[j], 0f};
            tri(p, nr, c, ctr, v1, v0, n, n, n, col, col, col);
        }
        return new Mesh(p.trim(), nr.trim(), c.trim());
    }
}
