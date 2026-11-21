"""Volunteer page text in English, Spanish and Simplified Chinese.

The Spanish and Chinese were machine-drafted: have a native speaker review them before real use.
Add a language by adding a column to every entry in STRINGS and a name in LANGS.
"""
LANGS = {"en": "English", "es": "Español", "zh": "中文"}

STRINGS = {
    "safety_banner": {
        "en": "**Look and report only.** Never clear drains during a storm or step into pooled water. "
              "Report blocked drains to 311.",
        "es": "**Solo mire y reporte.** Nunca limpie coladeras durante una tormenta ni pise agua estancada. "
              "Reporte las coladeras tapadas al 311.",
        "zh": "**只看不动手，然后报告。** 暴雨时绝不清理排水口，也不要踩进积水。堵塞的排水口请报告给 311。",
    },
    "title": {"en": "Check a drain near you", "es": "Revise una coladera cerca de usted", "zh": "检查您附近的排水口"},
    "i_am": {"en": "I am", "es": "Soy", "zh": "我是"},
    "new_volunteer": {"en": "New volunteer", "es": "Voluntario nuevo", "zh": "新志愿者"},
    "first_name": {"en": "First name (no last names or addresses)", "es": "Nombre (sin apellidos ni direcciones)",
                   "zh": "名字（不要填姓氏或地址）"},
    "how_far": {"en": "How far will you walk? (meters)", "es": "¿Qué tan lejos caminará? (metros)",
                "zh": "您愿意走多远？（米）"},

    # Starting spot
    "where_title": {"en": "Where will you start walking from?", "es": "¿Desde dónde empezará a caminar?",
                    "zh": "您从哪里出发？"},
    "where_help": {
        "en": "Share your location, type an address or cross streets, or tap the map. "
              "We only save the map point, not your address.",
        "es": "Comparta su ubicación, escriba una dirección o un cruce de calles, o toque el mapa. "
              "Solo guardamos el punto en el mapa, no su dirección.",
        "zh": "分享您的位置、输入地址或路口，或点击地图。我们只保存地图上的点，不保存您的地址。",
    },
    "share_location": {"en": "Share my location", "es": "Compartir mi ubicación", "zh": "分享我的位置"},
    "share_wait": {"en": "Waiting for your phone... tap Allow if it asks.",
                   "es": "Esperando a su teléfono... toque Permitir si se lo pide.",
                   "zh": "正在获取位置……如果手机询问，请点“允许”。"},
    "share_failed": {"en": "Couldn't get your location. Type an address or tap the map instead.",
                     "es": "No pudimos obtener su ubicación. Escriba una dirección o toque el mapa.",
                     "zh": "无法获取您的位置。请输入地址或点击地图。"},
    "address": {"en": "Address or cross streets", "es": "Dirección o cruce de calles", "zh": "地址或路口"},
    "address_ph": {"en": "e.g. 24th St & Mission St", "es": "ej. 24th St & Mission St", "zh": "例如 24th St & Mission St"},
    "find": {"en": "Find", "es": "Buscar", "zh": "查找"},
    "address_failed": {"en": "Couldn't find that in San Francisco. Try cross streets, or tap the map.",
                       "es": "No lo encontramos en San Francisco. Pruebe un cruce de calles o toque el mapa.",
                       "zh": "在旧金山找不到该地址。请试试路口，或点击地图。"},
    "outside_sf": {"en": "That spot is outside San Francisco. Pick a spot in the city.",
                   "es": "Ese punto está fuera de San Francisco. Elija un punto en la ciudad.",
                   "zh": "该地点不在旧金山市内，请选择市内的地点。"},
    "tap_map": {"en": "Or tap the map to drop your pin exactly.", "es": "O toque el mapa para poner su punto exacto.",
                "zh": "或者点击地图精确放置您的位置。"},
    "spot_set": {"en": "Starting spot: {label}", "es": "Punto de partida: {label}", "zh": "出发地点：{label}"},
    "spot_from_gps": {"en": "your current location", "es": "su ubicación actual", "zh": "您的当前位置"},
    "spot_from_map": {"en": "the pin you dropped", "es": "el punto que marcó", "zh": "您放置的图钉"},
    "need_spot": {"en": "Set a starting spot to sign up.", "es": "Elija un punto de partida para inscribirse.",
                  "zh": "请先设置出发地点再注册。"},
    "sign_up": {"en": "Sign up", "es": "Inscribirme", "zh": "注册"},

    # Signed in
    "alerts_title": {"en": "Get alerts on this phone", "es": "Recibir alertas en este teléfono", "zh": "在此手机上接收提醒"},
    "alerts_body": {
        "en": "1. Install the free **ntfy** app.  2. Subscribe to topic **`{topic}`**, or scan this code.",
        "es": "1. Instale la app gratuita **ntfy**.  2. Suscríbase al tema **`{topic}`** o escanee este código.",
        "zh": "1. 安装免费的 **ntfy** 应用。 2. 订阅主题 **`{topic}`**，或扫描此二维码。",
    },
    "near_title": {"en": "Drains near you ({n})", "es": "Coladeras cerca de usted ({n})", "zh": "您附近的排水口（{n}）"},
    "none_near": {"en": "Nothing to check within your walking distance right now. Thanks! We'll alert you when that changes.",
                  "es": "No hay nada que revisar cerca por ahora. ¡Gracias! Le avisaremos si cambia.",
                  "zh": "您步行范围内暂时没有需要检查的。谢谢！有变化时我们会提醒您。"},
    "card_dist": {"en": "{m} m, about {mins} min walk", "es": "{m} m, unos {mins} min a pie", "zh": "{m} 米，步行约 {mins} 分钟"},
    "tier_Urgent": {"en": "Urgent", "es": "Urgente", "zh": "紧急"},
    "tier_High": {"en": "High", "es": "Alta", "zh": "高"},
    "tier_Watch": {"en": "Watch", "es": "Vigilar", "zh": "关注"},
    "open_maps": {"en": "Open in Maps", "es": "Abrir en Mapas", "zh": "在地图中打开"},
    "btn_clear": {"en": "Checked - clear", "es": "Revisada: despejada", "zh": "已查：通畅"},
    "btn_blocked": {"en": "Blocked", "es": "Tapada", "zh": "堵塞"},
    "btn_cant": {"en": "Couldn't reach", "es": "No pude llegar", "zh": "无法到达"},
    "thanks": {"en": "Thanks! Logged.", "es": "¡Gracias! Registrado.", "zh": "谢谢！已记录。"},
    "logged_retry": {"en": "Logged. Someone else will try.", "es": "Registrado. Otra persona lo intentará.",
                     "zh": "已记录。其他人会再试。"},
    "blocked_msg": {"en": "Drain {id} marked blocked. Please also report it to the city:",
                    "es": "Coladera {id} marcada como tapada. Por favor repórtela también a la ciudad:",
                    "zh": "排水口 {id} 已标记为堵塞。请同时报告给市政府："},
    "report_311": {"en": "Report to SF 311", "es": "Reportar al SF 311", "zh": "报告给 SF 311"},
}


def t(key, lang="en", **kw):
    entry = STRINGS.get(key, {})
    text = entry.get(lang) or entry.get("en") or key
    return text.format(**kw) if kw else text
