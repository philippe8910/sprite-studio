# -*- coding: utf-8 -*-
"""模型清單、價格、與提示詞預設。

提示詞配方直接沿用 pixel-sprite-pipeline 技能裡的血淚教訓：
- 動作提示詞不假設角色帶什麼裝備；攻擊類禁特效並限定一次一下
- 待機不要寫 "idle animation"（會被重新詮釋成轉身站姿）
- 一律鎖側面朝向 + 綠幕尾綴
{C} 會被專案的「角色描述」取代。
"""

# ---------------------------------------------------------------- 模型

IMAGE_MODELS = {
    "nano-banana": {
        "id": "fal-ai/nano-banana/edit",
        "label": "Nano Banana Edit（Gemini 2.5 Flash Image）",
        "price": 0.039,
        "note": "多圖輸入最穩、最會照抄角色特徵，姿勢重繪首選",
        "extra": {"output_format": "png", "aspect_ratio": "1:1"},
    },
    "seedream4": {
        "id": "fal-ai/bytedance/seedream/v4/edit",
        "label": "Seedream v4 Edit",
        "price": 0.03,
        "note": "像素風筆觸乾淨，偶爾會改動服裝細節",
        "extra": {"image_size": {"width": 2048, "height": 2048}},
    },
    "kontext-max": {
        "id": "fal-ai/flux-pro/kontext/max/multi",
        "label": "FLUX.1 Kontext Max（multi）",
        "price": 0.08,
        "note": "構圖服從度高，價格較貴",
        "extra": {"output_format": "png", "aspect_ratio": "1:1"},
    },
}

VIDEO_MODELS = {
    "kling25": {
        "id": "fal-ai/kling-video/v2.5-turbo/pro/image-to-video",
        "label": "Kling 2.5 Turbo Pro",
        "price": 0.35,
        "negative": True,
        "durations": ["5", "10"],
        "keeps_aspect": True,
        "note": "循環動作（idle / run / walk）首選：有負面提示詞可以擋轉身",
        "best_for": ["idle", "walk", "run", "crouch_idle"],
    },
    "seedance-fast": {
        "id": "fal-ai/bytedance/seedance/v1/pro/fast/image-to-video",
        "label": "Seedance v1 Pro Fast",
        "price": 0.086,
        "negative": False,
        "durations": ["3", "4", "5", "6"],
        "keeps_aspect": False,
        "note": "單次動作（jump / hurt / attack）便宜又夠服從；會裁成 1:1",
        "best_for": ["jump", "hurt", "attack", "crouch_attack", "death", "roll"],
    },
}

# ---------------------------------------------------------------- 共用句型

GREEN_TAIL = ("Camera fixed, no zoom, plain solid green screen background stays unchanged. "
              "No particle effects, no objects appear.")

NEG_COMMON = ("turning around, rotating body, facing the camera, front view, 3/4 view, "
              "camera movement, zoom, pan, background change, particle effects, "
              "flashes, smoke, sparks")

# ---------------------------------------------------------------- 角色生成的共用句型
# 立繪一律從這裡生（不再支援上傳外部圖）。有勾「風格參考」時參考圖會一起送進模型，
# STYLE_LINE 會叫模型照抄參考圖的畫風（而不是抄它的角色）。

CHAR_TAIL = ("Full body from head to feet, feet fully visible, character centered with a small "
             "margin, plain solid green screen background (#40b100), flat even lighting, "
             "no shadow on the ground, no text, no watermark, no border, no UI.")

CHAR_NEG = ("cropped legs, cut off feet, close-up, portrait crop, multiple characters, "
            "extra limbs, extra fingers, text, watermark, signature, logo, busy background, "
            "scenery, furniture, drop shadow, blurry")

STYLE_LINE = ("Match the art style of the style reference image exactly — same rendering "
              "technique, same line weight, same shading and the same colour treatment — but "
              "draw the character described here, not the character in the reference. ")

# ---------------------------------------------------------------- 角色生成：可組合的提示詞
# 一句提示詞拆成四段自己選：風格(style) / 角度(angle) / 面向(facing) / 姿勢(pose)。
# 前端照這個順序組：
#     {style.head}, {angle.en}, {facing.en}, {pose.en}. {style.tail} {額外描述} + CHAR_TAIL
# 負面詞則是 CHAR_NEG + 各段自己的 neg。
# 姿勢可以自己新增，自訂的存在 settings.json 的 char_poses（不是存在專案裡，所有專案共用）。

CHAR_STYLES = [
    {
        "key": "pixel",
        "label": "像素風 sprite（遊戲用）",
        "head": "Pixel art game character sprite of {C}",
        "tail": ("Clean crisp pixel art with a limited palette and hard pixel edges, no "
                 "anti-aliased soft glow, drawn as a game character reference."),
        "neg": "motion blur, soft glow, painterly brush strokes, 3d render",
    },
    {
        "key": "pixel_hd",
        "label": "高解析像素風（細節多）",
        "head": "High resolution pixel art character sprite of {C}",
        "tail": ("Detailed pixel art with careful dithering and clean hard edges, rich but "
                 "still limited palette, no blur."),
        "neg": "motion blur, soft glow, painterly brush strokes, 3d render",
    },
    {
        "key": "anime",
        "label": "動漫平塗立繪（人設用）",
        "head": "Anime character full body reference illustration of {C}",
        "tail": "Clean flat anime colouring with crisp outlines, even lighting.",
        "neg": "sketchy lines, unfinished lineart, 3d render",
    },
    {
        "key": "style_ref",
        "label": "照風格參考圖重畫（要放參考圖）",
        "head": "Game character sprite of {C}",
        "tail": STYLE_LINE,
        "neg": ("copying the reference character's face, copying the reference character's "
                "outfit, different art style from the reference"),
        "needs_ref": True,
    },
    {
        "key": "repaint",
        "label": "把參考圖的角色轉成像素風（要放參考圖）",
        "head": ("Redraw the character in the reference image as a clean pixel art game sprite: "
                 "same character, same hair, same colours, same outfit, same equipment {C}"),
        "tail": "Limited palette, hard pixel edges, no anti-aliased glow.",
        "neg": ("changing the outfit, changing hair colour, adding accessories, "
                "different character"),
        "needs_ref": True,
    },
]

CHAR_ANGLES = [
    {
        "key": "side",
        "label": "正側面（側捲軸動畫首選）",
        "en": "in a strict side profile view with the camera at eye level",
        "neg": "front view, 3/4 view, facing the camera, tilted camera",
        "tip": "動畫管線只吃這個角度：之後的姿勢、影片、打包都假設角色是正側面。",
    },
    {
        "key": "three_quarter",
        "label": "3/4 前側（立繪／人設常用）",
        "en": ("in a three-quarter front view, the body turned roughly 30 degrees away from the "
               "camera so the chest and the face both read clearly"),
        "neg": "strict side profile, back view, tilted camera",
        "tip": "3/4 好看但不能直接做側面動畫——要做動畫先去「姿勢修改」轉成側面 stand。",
    },
    {
        "key": "front",
        "label": "正面",
        "en": "facing the camera straight on in a symmetrical frontal view",
        "neg": "3/4 view, side profile, back view, foreshortening",
        "skip_facing": True,
        "tip": "正面立繪同上：要做動畫得先轉側面。",
    },
    {
        "key": "three_quarter_back",
        "label": "3/4 後側",
        "en": "in a three-quarter back view, seen from behind at roughly 30 degrees",
        "neg": "front view, facing the camera",
        "tip": "背向角度只適合做人設參考，動畫管線用不到。",
    },
    {
        "key": "back",
        "label": "背面",
        "en": "seen directly from behind in a straight back view",
        "neg": "front view, facing the camera, side profile",
        "skip_facing": True,
        "tip": "背向角度只適合做人設參考，動畫管線用不到。",
    },
    {
        "key": "top_down",
        "label": "俯視 45°（俯視角遊戲）",
        "en": "seen from a 45 degree top-down angle like an isometric game character",
        "neg": "eye level camera, side profile",
        "skip_facing": True,
        "tip": "俯視角是另一種遊戲用法，這個工具後面的動畫管線不支援。",
    },
]

CHAR_FACINGS = [
    {"key": "right", "label": "朝右", "en": "facing right",
     "neg": "facing left, mirrored"},
    {"key": "left", "label": "朝左", "en": "facing left",
     "neg": "facing right, mirrored"},
]

# 姿勢＝任何遊戲角色都有的通用動作組，刻意不假設角色帶什麼裝備（拿刀、拿槍、空手都適用）。
# 角色手上有什麼一律寫在「角色描述」({C})，這裡只描述身體怎麼動。
CHAR_POSES = [
    {"key": "idle", "label": "站立待機",
     "en": "standing upright with the weight on both feet, arms relaxed at the sides",
     "neg": "dynamic pose, action pose"},
    {"key": "walk", "label": "走路",
     "en": "mid stride walking, one leg forward and one leg back, arms swinging naturally",
     "neg": "standing still, both feet together"},
    {"key": "run", "label": "跑步",
     "en": "in a running stride leaning forward, one leg driving forward and the other extended "
           "back, arms bent and swinging",
     "neg": "standing still, both feet together, walking slowly"},
    {"key": "jump", "label": "跳躍",
     "en": "in mid air at the top of a jump, both feet off the ground and the knees pulled up",
     "neg": "standing on the ground, feet planted"},
    {"key": "attack", "label": "攻擊",
     "en": "in the middle of an attack, the body committed forward into the strike, torso "
           "twisted and the leading arm extended",
     "neg": "idle pose, standing still, impact effects, sparks, motion trails"},
    {"key": "hurt", "label": "受傷",
     "en": "recoiling from a hit, head and torso thrown backwards, arms flung out, one foot "
           "sliding back",
     "neg": "idle pose, attacking, blood, impact effects"},
    {"key": "crouch", "label": "蹲下",
     "en": "crouching low with both knees deeply bent and the torso lowered, feet still under "
           "the body",
     "neg": "standing upright, kneeling on the ground"},
    {"key": "crouch_attack", "label": "蹲下攻擊",
     "en": "crouching low with the knees bent and attacking forward from the crouch, the "
           "leading arm extended low",
     "neg": "standing upright, idle pose, impact effects, sparks"},
    {"key": "none", "label": "不指定姿勢", "en": "", "neg": ""},
]


def char_parts(custom_poses=None):
    """給前端的四段選項；custom_poses 是使用者自己加的姿勢（settings.json）。"""
    poses = [dict(p) for p in CHAR_POSES]
    for c in (custom_poses or []):
        if not (c.get("key") and c.get("label")):
            continue
        poses.insert(-1, {"key": c["key"], "label": c["label"],
                          "en": c.get("en", ""), "neg": c.get("neg", ""), "custom": True})
    return {"style": CHAR_STYLES, "angle": CHAR_ANGLES, "facing": CHAR_FACINGS, "pose": poses,
            "tail": CHAR_TAIL, "neg": CHAR_NEG}


# ---------------------------------------------------------------- 姿勢預設（生成參考立繪用）

# 通用動作組：不假設角色帶什麼裝備（空手／刀劍／槍械都適用）。角色手上有什麼、怎麼拿，
# 一律靠「保持設計 100% 相同」這句從立繪繼承，提示詞本身不提武器。
POSE_HEAD = "Redraw the character from the first image in the pose of the reference image: "

POSE_KEEP = ("Keep the character's design 100% identical — same hair, same colours, same outfit, "
             "same equipment held in the same hands, same art style and the same line thickness. "
             "Full body, feet visible, plain solid green screen background (#40b100), centered, "
             "no shadow, no text.")

POSE_PRESETS = [
    {
        "key": "stand",
        "label": "站立待機",
        "prompt": (POSE_HEAD + "standing upright in strict side profile facing right, weight on "
                   "both feet, arms relaxed and lowered. " + POSE_KEEP),
    },
    {
        "key": "walk",
        "label": "走路中段",
        "prompt": (POSE_HEAD + "mid-stride walking pose, strict side profile facing right, torso "
                   "upright, one leg forward and one leg back, arms swinging naturally. "
                   + POSE_KEEP),
    },
    {
        "key": "run",
        "label": "跑步中段",
        "prompt": (POSE_HEAD + "mid-stride running pose, strict side profile facing right, torso "
                   "upright and vertical, front knee lifted, rear leg extended back. " + POSE_KEEP
                   + " No motion blur, no speed lines."),
    },
    {
        "key": "jump",
        "label": "跳躍收腿",
        "prompt": (POSE_HEAD + "airborne jump pose, strict side profile facing right, knees "
                   "tucked up, arms held close to the body. " + POSE_KEEP),
    },
    {
        "key": "attack",
        "label": "攻擊",
        "prompt": (POSE_HEAD + "attacking forward, strict side profile facing right, the body "
                   "committed into the strike, torso twisted and the leading arm extended "
                   "forward. " + POSE_KEEP + " No impact effects, no sparks, no motion trails."),
    },
    {
        "key": "hurt",
        "label": "受傷",
        "prompt": (POSE_HEAD + "recoiling from a hit, strict side profile facing right, head and "
                   "torso thrown backwards, arms flung out, one foot sliding back. " + POSE_KEEP
                   + " No blood, no impact effects."),
    },
    {
        "key": "crouch",
        "label": "蹲下待機",
        "prompt": (POSE_HEAD + "crouching low in strict side profile facing right, both knees "
                   "deeply bent, torso lowered and back straight, arms lowered. " + POSE_KEEP),
    },
    {
        "key": "crouch_attack",
        "label": "蹲下攻擊",
        "prompt": (POSE_HEAD + "crouching low in strict side profile facing right, knees bent, "
                   "attacking forward from the crouch with the leading arm extended low. "
                   + POSE_KEEP + " No impact effects, no sparks."),
    },
    {
        "key": "custom",
        "label": "自訂（空白）",
        "prompt": POSE_HEAD.rstrip(": ") + ". " + POSE_KEEP,
    },
]

# ---------------------------------------------------------------- 動作預設（生成影片用）
# pose = 建議使用的姿勢圖 key；model = 建議模型；fps/loop/align = 打包預設

ANIM_PRESETS = [
    {
        "key": "idle",
        "label": "待機 idle",
        "pose": "stand",
        "model": "kling25",
        "fps": 8, "loop": True, "align": False,
        "prompt": ("Pixel art character {C}, standing in strict side profile facing right, arms "
                   "lowered and relaxed, holding whatever it already holds in exactly the same "
                   "way the entire time. The only motion is very subtle breathing: chest and "
                   "shoulders rise and fall gently, hair and accessories sway slightly. Feet "
                   "planted, size and position constant, seamless loop. Completely static "
                   "locked-off camera, plain solid green screen background stays unchanged."),
        "neg": NEG_COMMON + ", walking, stepping, zooming, scale change",
        "tip": "不要寫 idle animation，會被重新詮釋成轉身站姿。只描述呼吸起伏。",
    },
    {
        "key": "walk",
        "label": "走路 walk",
        "pose": "stand",
        "model": "kling25",
        "fps": 10, "loop": True, "align": False,
        "prompt": ("Pixel art character {C}, walking in place, classic side-scroller walk cycle, "
                   "strict side profile facing right. Upright posture, torso vertical, calm even "
                   "steps with a moderate stride, arms swinging naturally. Stays centered on the "
                   "same spot, seamless loop. Plain solid green screen background stays "
                   "unchanged."),
        "neg": NEG_COMMON + ", running, sprinting, leaning forward, moving across the frame, dust",
        "tip": "走路和跑步差在步幅與身體前傾——寫 calm even steps，不然模型會直接給你跑步。",
    },
    {
        "key": "run",
        "label": "跑步 run",
        "pose": "stand",
        "model": "kling25",
        "fps": 12, "loop": True, "align": False,
        "prompt": ("Pixel art character {C}, running in place, classic side-scroller run cycle, "
                   "strict side profile facing right. Upright posture, torso vertical, legs "
                   "stride rhythmically with knees lifting, hair and accessories bounce with each "
                   "step. Constant even tempo, stays centered on the same spot, seamless loop. "
                   "Plain solid green screen background stays unchanged."),
        "neg": NEG_COMMON + ", leaning forward heavily, tilted body, crooked posture, "
               "hunched over, stumbling, moving across the frame, dust",
        "tip": "Seedance 跑步會歪身變 3/4，這支一律用 Kling。挑幀時用循環偵測找週期。",
    },
    {
        "key": "attack",
        "label": "攻擊 attack",
        "pose": "attack",
        "model": "seedance-fast",
        "fps": 20, "loop": False, "align": False,
        "prompt": ("Pixel art character {C}, strict side profile facing right. It performs "
                   "exactly one single forward attack to the right: a short wind-up backward, "
                   "then the body commits forward into the strike with the leading arm extending "
                   "to the right, then it settles back to the standing pose. Feet stay on the "
                   "same spot. Nothing is emitted: no flash, no light, no smoke, no sparks, no "
                   "trails. " + GREEN_TAIL),
        "neg": NEG_COMMON + ", multiple attacks, impact effects, slash trails, motion trails, "
               "energy blast, projectile",
        "tip": "一次只做一下：寫 exactly one single attack。特效一律禁掉，遊戲端自己疊。",
    },
    {
        "key": "jump",
        "label": "跳躍 jump（單一動作）",
        "pose": "stand",
        "model": "seedance-fast",
        "fps": 16, "loop": False, "align": True,
        "prompt": ("Pixel art character {C}, strict side profile facing right. It performs exactly "
                   "one single quick vertical jump on the spot: a short crouch to charge, then it "
                   "springs straight up with the knees pulled up, and lands back on the same spot "
                   "absorbing the impact with bent knees, then straightens back to standing. One "
                   "compact jump at normal speed, no extra hops, it never turns and never moves "
                   "sideways. No dust. " + GREEN_TAIL),
        "neg": NEG_COMMON + ", multiple jumps, small hops, moving forward, slow motion, floating, "
               "dust, motion blur",
        "tip": "直接當一個動作用的短跳，不用拆段。要拆 rise/fall/land 的話改用「跳躍全段」。",
    },
    {
        "key": "jump_full",
        "label": "跳躍全段 jump_full（可拆 rise/fall/land）",
        "pose": "stand",
        "model": "seedance-fast",
        "fps": 20, "loop": False, "align": True,
        "prompt": ("Pixel art character {C}. It performs exactly one single very high vertical "
                   "jump in slow motion, floaty like low gravity: first it crouches deeply to "
                   "charge, then launches straight up, knees tucked up high while rising; at the "
                   "top it floats for a moment; then it falls slowly with the legs extended "
                   "downward, hair fluttering upward; it lands on the same spot bending the "
                   "knees deeply, then stands back up. Exactly one jump, no extra hops, strict "
                   "side profile, never turns. No dust. " + GREEN_TAIL),
        "neg": NEG_COMMON + ", multiple jumps, small hops, moving forward, dust, motion blur",
        "tip": "慢動作高跳，專門拿來切成 rise / fall / land：上升收腿、下落伸腿寫成相反視覺特徵才好切。"
               "抽完幀用膠片的「跳躍分段」或「另存為動作」拆；打包記得開逐幀腳底對齊。",
    },
    {
        "key": "hurt",
        "label": "受傷 hurt",
        "pose": "stand",
        "model": "seedance-fast",
        "fps": 18, "loop": False, "align": False,
        "prompt": ("Pixel art character {C}. It flinches as if struck: recoils backward slightly, "
                   "head snaps back, hair swings, then recovers back to the original standing "
                   "pose. Feet stay on the same spot. No blood. " + GREEN_TAIL),
        "neg": NEG_COMMON + ", falling down, lying on the ground, blood",
        "tip": "只取「被打到 → 回正」那段，回正後多的幀不要。",
    },
    {
        "key": "crouch_idle",
        "label": "蹲待機 crouch_idle",
        "pose": "crouch",
        "model": "kling25",
        "fps": 5, "loop": True, "align": False,
        "prompt": ("Pixel art character {C}, crouching low in strict side profile facing right, "
                   "knees bent and arms lowered, staying crouched the entire time and holding "
                   "whatever it already holds in exactly the same way. The only motion is gentle "
                   "breathing, hair and accessories sway subtly. Seamless loop. Plain solid "
                   "green screen background stays unchanged."),
        "neg": NEG_COMMON + ", standing up, walking, stepping",
        "tip": "蹲姿源圖比例常和站姿不一致，打包時用 per-clip 縮放校正。",
    },
    {
        "key": "crouch_attack",
        "label": "蹲下攻擊 crouch_attack",
        "pose": "crouch_attack",
        "model": "seedance-fast",
        "fps": 20, "loop": False, "align": False,
        "prompt": ("Pixel art character {C}, crouching low in strict side profile facing right. "
                   "It performs exactly one single forward attack to the right from the crouch: "
                   "a short wind-up, then the leading arm extends forward low, then it settles "
                   "back to the crouched pose. It stays crouched in place the whole time and "
                   "never stands up. Nothing is emitted: no flash, no light, no smoke, no "
                   "sparks. " + GREEN_TAIL),
        "neg": NEG_COMMON + ", standing up, multiple attacks, impact effects, slash trails, "
               "energy blast, projectile",
        "tip": "最容易失敗的是「攻擊到一半站起來」——負面詞的 standing up 不要拿掉。",
    },
    {
        "key": "roll",
        "label": "翻滾 roll",
        "pose": "stand",
        "model": "seedance-fast",
        "fps": 20, "loop": False, "align": True,
        "prompt": ("Pixel art character {C}, strict side profile facing right. From a standing "
                   "start it dives forward into one single low forward roll along the ground to "
                   "the right: it ducks, tucks the head and knees, rolls over the shoulder and "
                   "back, and comes back up to standing facing right. It only ever moves to the "
                   "right, it never rolls backward. Exactly one roll, low to the ground, no "
                   "jumping, knees tucked tight. " + GREEN_TAIL),
        "neg": NEG_COMMON + ", jumping, leaping into the air, rolling to the left, backflip, "
               "multiple rolls, dust",
        "tip": "站姿起手 + 只往右 + 鎖側面 + 收腿，這四句是實測可用配方。",
    },
    {
        "key": "death",
        "label": "死亡 death",
        "pose": "stand",
        "model": "seedance-fast",
        "fps": 14, "loop": False, "align": True,
        "prompt": ("Pixel art character {C}, strict side profile facing right. It is struck, "
                   "staggers backward, the body goes limp, it drops to its knees and then falls "
                   "onto the ground and stops moving. One single continuous motion, it stays in "
                   "the same spot horizontally. No blood. " + GREEN_TAIL),
        "neg": NEG_COMMON + ", blood, gore, standing back up, disappearing",
        "tip": "倒地後重心會亂跑，打包對齊要用首幀而不是中位數。",
    },
    {
        "key": "custom",
        "label": "自訂動作",
        "pose": "stand",
        "model": "kling25",
        "fps": 12, "loop": True, "align": False,
        "prompt": "Pixel art character {C}, strict side profile facing right. " + GREEN_TAIL,
        "neg": NEG_COMMON,
        "tip": "自己寫。記得結尾保留綠幕尾綴，並鎖住朝向。",
    },
]


def fill(text, character):
    return (text or "").replace("{C}", (character or "").strip())


def anim_preset(key):
    for p in ANIM_PRESETS:
        if p["key"] == key:
            return p
    return None


def pose_preset(key):
    for p in POSE_PRESETS:
        if p["key"] == key:
            return p
    return None
