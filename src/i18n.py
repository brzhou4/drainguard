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
    "none_near": {"en": "No priority drains need checking right now. Thanks!",
                  "es": "Por ahora no hay coladeras prioritarias que revisar. ¡Gracias!",
                  "zh": "目前没有需要检查的重点排水口。谢谢！"},
    "card_dist": {"en": "{m} m, about {mins} min walk", "es": "{m} m, unos {mins} min a pie", "zh": "{m} 米，步行约 {mins} 分钟"},
    "tier_Urgent": {"en": "Urgent", "es": "Urgente", "zh": "紧急"},
    "tier_High": {"en": "High", "es": "Alta", "zh": "高"},
    "tier_Watch": {"en": "Watch", "es": "Vigilar", "zh": "关注"},
    "open_maps": {"en": "Open in Maps", "es": "Abrir en Mapas", "zh": "在地图中打开"},
    "btn_blocked": {"en": "Blocked", "es": "Tapada", "zh": "堵塞"},
    "btn_cant": {"en": "Couldn't reach", "es": "No pude llegar", "zh": "无法到达"},
    "thanks": {"en": "Thanks! Logged.", "es": "¡Gracias! Registrado.", "zh": "谢谢！已记录。"},
    "logged_retry": {"en": "Logged. Someone else will try.", "es": "Registrado. Otra persona lo intentará.",
                     "zh": "已记录。其他人会再试。"},
    "blocked_msg": {"en": "Drain {id} marked blocked. Now send it to the city, it takes about a minute.",
                    "es": "Coladera {id} marcada como tapada. Ahora envíela a la ciudad, toma como un minuto.",
                    "zh": "排水口 {id} 已标记为堵塞。现在报告给市政府，大约需要一分钟。"},
    "report_311": {"en": "Open the SF 311 sewer report", "es": "Abrir el reporte de alcantarillado del SF 311",
                   "zh": "打开 SF 311 下水道报告"},
    "h311_steps": {
        "en": "1. Tap the button. **The drain's location, type and description are already filled in.**\n"
              "2. Add your contact info if you want updates from the city (optional).\n"
              "3. Submit the report on the city page.",
        "es": "1. Toque el botón. **La ubicación, el tipo y la descripción de la coladera ya están llenos.**\n"
              "2. Agregue sus datos de contacto si quiere novedades de la ciudad (opcional).\n"
              "3. Envíe el reporte en la página de la ciudad.",
        "zh": "1. 点击按钮。**排水口的位置、类型和描述都已填好。**\n"
              "2. 如果想收到市政府的进展通知，可以填写联系方式（可选）。\n"
              "3. 在市政页面上提交报告。",
    },
    "h311_copy": {"en": "If the description is missing on the city form, copy it from here (in English for city staff):",
                  "es": "Si la descripción no aparece en el formulario de la ciudad, cópiela de aquí (en inglés para el personal de la ciudad):",
                  "zh": "如果市政表格里没有描述，请从这里复制（英文，方便市政人员阅读）："},
    "h311_done": {"en": "I sent it to 311", "es": "Ya lo envié al 311", "zh": "我已报告给 311"},

    # Ported volunteer features (anonymous volunteers, storm lock, walking route, reports)
    "tab_back": {"en": "I already signed up", "es": "Ya me inscribí", "zh": "我已经注册过"},
    "paste_link": {"en": "Paste your private link (or code)", "es": "Pegue su enlace privado (o código)", "zh": "粘贴您的专属链接（或代码）"},
    "open_link": {"en": "Continue", "es": "Continuar", "zh": "继续"},
    "link_not_found": {"en": "That link wasn't found. Check it, or sign up again.", "es": "No encontramos ese enlace. Revíselo o inscríbase de nuevo.", "zh": "找不到该链接。请检查，或重新注册。"},
    "age_q": {"en": "Age", "es": "Edad", "zh": "年龄"},
    "age_adult": {"en": "I'm 18 or older", "es": "Tengo 18 años o más", "zh": "我已满 18 岁"},
    "age_minor": {"en": "I'm under 18 and will always go with an adult", "es": "Tengo menos de 18 y siempre iré con un adulto", "zh": "我未满 18 岁，会一直有成年人陪同"},
    "safety_agree": {"en": "I'll only look from the sidewalk, in daylight, and never during a storm.", "es": "Solo miraré desde la banqueta, de día, y nunca durante una tormenta.", "zh": "我只在白天从人行道上查看，暴雨时绝不出门。"},
    "safety_rules_title": {"en": "Safety rules", "es": "Reglas de seguridad", "zh": "安全规则"},
    "safety_rules": {"en": "- Go in daylight.\n- Stay on the sidewalk. Never stand in the street or a bike lane.\n- Don't touch, lift or clear the grate. Just look and report.\n- Never walk into pooled or moving water.\n- Under 18? Always bring an adult.\n- If anything feels unsafe, skip it and tap **Couldn't reach**.", "es": "- Salga de día.\n- Quédese en la banqueta. Nunca se pare en la calle ni en el carril de bicis.\n- No toque, levante ni limpie la rejilla. Solo mire y reporte.\n- Nunca camine en agua estancada o corriente.\n- ¿Menor de 18? Siempre vaya con un adulto.\n- Si algo se siente inseguro, sáltelo y toque **No pude llegar**.", "zh": "- 白天出门。\n- 留在人行道上，绝不站在马路或自行车道上。\n- 不要触碰、掀开或清理格栅，只看并报告。\n- 绝不走进积水或流水中。\n- 未满 18 岁？一定要有成年人陪同。\n- 感觉不安全就跳过，点 **无法到达**。"},
    "your_link_title": {"en": "Your private link (save it!)", "es": "Su enlace privado (¡guárdelo!)", "zh": "您的专属链接（请保存！）"},
    "sign_out": {"en": "Sign out on this phone", "es": "Cerrar sesión en este teléfono", "zh": "在此手机上退出"},
    "minor_reminder": {"en": "Reminder: bring your adult with you on every walk.", "es": "Recuerde: lleve a su adulto en cada caminata.", "zh": "提醒：每次出门都要有成年人陪同。"},
    "m_you": {"en": "You checked", "es": "Usted revisó", "zh": "您已检查"},
    "m_you_help": {"en": "This round (checks count for {days} days). All time: {total}.", "es": "En esta ronda (las revisiones cuentan {days} días). En total: {total}.", "zh": "本轮（每次检查有效 {days} 天）。累计：{total}。"},
    "m_city": {"en": "Urgent drains checked (city)", "es": "Urgentes revisadas (ciudad)", "zh": "全市紧急排水口已检查"},
    "m_area": {"en": "Checked near you", "es": "Revisadas cerca", "zh": "附近已检查"},
    "round_note": {"en": "Counting checks since {since}.", "es": "Contando revisiones desde {since}.", "zh": "统计 {since} 以来的检查。"},
    "adopt": {"en": "Want a drain of your own? Join SF Adopt-a-Drain", "es": "¿Quiere su propia coladera? Únase a SF Adopt-a-Drain", "zh": "想认领一个排水口？加入 SF Adopt-a-Drain"},
    "report_title": {"en": "Report stop {stop}", "es": "Reportar parada {stop}", "zh": "报告第 {stop} 站"},
    "what_saw": {"en": "What did you find?", "es": "¿Qué encontró?", "zh": "您看到了什么？"},
    "st_Checked - clear": {"en": "Clear: water can flow in", "es": "Despejada: el agua puede entrar", "zh": "通畅：水可以流进去"},
    "st_Blocked": {"en": "Blocked or mostly covered", "es": "Tapada o casi cubierta", "zh": "堵塞或大部分被盖住"},
    "st_Couldn't reach": {"en": "Couldn't reach it safely", "es": "No pude llegar de forma segura", "zh": "无法安全到达"},
    "condition": {"en": "What's on it? (optional)", "es": "¿Qué tiene encima? (opcional)", "zh": "上面有什么？（可选）"},
    "note": {"en": "Note (optional)", "es": "Nota (opcional)", "zh": "备注（可选）"},
    "photo": {"en": "Photo (optional; no faces or license plates)", "es": "Foto (opcional; sin caras ni placas)", "zh": "照片（可选；不要拍到人脸或车牌）"},
    "photo_too_big": {"en": "Photo is over {mb} MB. Try a smaller one.", "es": "La foto pesa más de {mb} MB. Pruebe una más pequeña.", "zh": "照片超过 {mb} MB，请换一张小一点的。"},
    "submit": {"en": "Send report", "es": "Enviar reporte", "zh": "提交报告"},
    "show_more": {"en": "Show up to 10 drains", "es": "Mostrar hasta 10 coladeras", "zh": "最多显示 10 个排水口"},
    "walk_total": {"en": "Stops: {n} · about {mins} min of walking (estimate)", "es": "Paradas: {n} · unos {mins} min caminando (estimado)", "zh": "共 {n} 站，步行约 {mins} 分钟（估计）"},
    "open_walk": {"en": "Open the whole walk in Google Maps", "es": "Abrir todo el recorrido en Google Maps", "zh": "在 Google 地图中打开整条路线"},
    "stop": {"en": "Stop {n}", "es": "Parada {n}", "zh": "第 {n} 站"},
    "leg": {"en": "about {mins} min from the last stop", "es": "unos {mins} min desde la parada anterior", "zh": "距上一站约 {mins} 分钟"},
    "leg_first": {"en": "about {mins} min from your starting spot", "es": "unos {mins} min desde su punto de partida", "zh": "距出发地点约 {mins} 分钟"},
    "attempts": {"en": "{n} volunteer(s) couldn't reach this one. Only try if it's safe.", "es": "{n} voluntario(s) no pudieron llegar. Inténtelo solo si es seguro.", "zh": "已有 {n} 位志愿者无法到达。仅在安全时尝试。"},
    "street_view": {"en": "Street View", "es": "Street View", "zh": "街景"},
    "release": {"en": "I'm not going after all", "es": "Al final no voy", "zh": "我不去了"},
    "youre_on_it": {"en": "you're heading there", "es": "usted va en camino", "zh": "您正在前往"},
    "tab_new": {"en": "Sign up", "es": "Inscribirme", "zh": "注册"},
    "anon_note": {"en": "No names: volunteers are anonymous. Your private link is how DrainGuard knows it's you.", "es": "Sin nombres: los voluntarios son anónimos. Su enlace privado es como DrainGuard sabe que es usted.", "zh": "不需要名字：志愿者都是匿名的。DrainGuard 通过您的专属链接识别您。"},
    "your_link_body": {"en": "Bookmark this link or add it to your home screen. It signs you in on any phone. Don't share it: anyone with it can report as you.", "es": "Guarde este enlace o agréguelo a su pantalla de inicio. Le permite entrar desde cualquier teléfono. No lo comparta: cualquiera con él puede reportar como usted.", "zh": "请收藏此链接或添加到主屏幕，可在任何手机上登录。请勿分享：拿到链接的人可以用您的身份报告。"},
    "claim": {"en": "I'm heading there (hides it from others for about {mins} min)", "es": "Voy en camino (la oculta a otros por unos {mins} min)", "zh": "我正在前往（对其他人隐藏约 {mins} 分钟）"},
    "claimed": {"en": "Hidden from others until {until}. Report it when you get there.", "es": "Oculta para otros hasta las {until}. Repórtela cuando llegue.", "zh": "在 {until} 之前对其他人隐藏。到达后请报告。"},
    "en_route_until": {"en": "you're heading there · hidden from others until {until}", "es": "usted va en camino · oculta para otros hasta las {until}", "zh": "您正在前往 · {until} 前对其他人隐藏"},
    "near_title": {"en": "Your walk ({n})", "es": "Su recorrido ({n})", "zh": "您的路线（{n}）"},
    "btn_clear": {"en": "Clear", "es": "Despejada", "zh": "通畅"},
    # Simple volunteer page: closest drain + 311 link
    "closest_title": {"en": "Closest drain to check", "es": "La coladera más cercana para revisar", "zh": "离您最近、需要检查的排水口"},
    "walk_there": {"en": "Walk there (Google Maps)", "es": "Caminar hasta allí (Google Maps)", "zh": "步行前往（Google 地图）"},
    "where_help_short": {"en": "Share your location, type an address or cross streets, or tap the map to see the closest drain.",
                         "es": "Comparta su ubicación, escriba una dirección o un cruce de calles, o toque el mapa para ver la coladera más cercana.",
                         "zh": "分享您的位置、输入地址或路口，或点击地图，查看离您最近的排水口。"},
    "report_311_general": {"en": "Report a sewer problem or flooding to SF 311", "es": "Reportar un problema de alcantarillado o inundación al SF 311",
                           "zh": "向 SF 311 报告下水道问题或积水"},
    "report_311_help": {"en": "Opens the city's sewer report form. Use it for blocked drains, sewer backups and street flooding. For an emergency, call 911.",
                        "es": "Abre el formulario de alcantarillado de la ciudad. Úselo para coladeras tapadas, desbordes del drenaje e inundaciones en la calle. En una emergencia, llame al 911.",
                        "zh": "打开市政府的下水道报告表。可用于排水口堵塞、下水道倒灌和街道积水。紧急情况请拨打 911。"},
}


def t(key, lang="en", **kw):
    entry = STRINGS.get(key, {})
    text = entry.get(lang) or entry.get("en") or key
    return text.format(**kw) if kw else text


CONDITION_LABELS = {
    "Leaves": {
        "es": "Hojas",
        "zh": "树叶"
    },
    "Trash": {
        "es": "Basura",
        "zh": "垃圾"
    },
    "Sediment / dirt": {
        "es": "Tierra / sedimento",
        "zh": "泥沙"
    },
    "Broken or missing grate": {
        "es": "Rejilla rota o faltante",
        "zh": "格栅损坏或缺失"
    },
    "Standing water": {
        "es": "Agua estancada",
        "zh": "积水"
    },
    "Other": {
        "es": "Otro",
        "zh": "其他"
    }
}


def condition_label(c, lang="en"):
    return CONDITION_LABELS.get(c, {}).get(lang, c)
