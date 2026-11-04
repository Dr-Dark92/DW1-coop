"""Original-style menu grid and native-link evolution chart, drawn over game video."""
import struct

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QBrush, QColor, QFont, QFontMetrics, QImage, QLinearGradient, QPainter, QPainterPath, QPen

from .camera import GPURAM_FIELD
from .live import ram_offset


def native_icons(core):
    """Decode the user's resident 4-bpp menu sprites; no game art is bundled."""
    state = core.serialize()
    marker = state.find(GPURAM_FIELD)
    if marker < 0:
        return ()
    start = marker + len(GPURAM_FIELD) + 4
    if struct.unpack_from("<I", state, marker + len(GPURAM_FIELD))[0] != 1024 * 512 * 2:
        return ()
    vram = state[start:start + 1024 * 512 * 2]
    if len(vram) != 1024 * 512 * 2:
        return ()
    sprites = ((0,0,12), (40,0,13), (0,20,12), (40,20,13), (0,40,13), (40,40,13), (80,0,13))
    images = []
    for u, v, clut in sprites:
        image = QImage(20, 20, QImage.Format.Format_ARGB32)
        for y in range(20):
            for x in range(20):
                word = struct.unpack_from("<H", vram, ((448 + v + y) * 1024 + 896 + (u + x) // 4) * 2)[0]
                index = (word >> (((u + x) % 4) * 4)) & 15
                color = struct.unpack_from("<H", vram, ((496 + clut) * 1024 + 256 + index) * 2)[0]
                r,g,b = color & 31, (color >> 5) & 31, (color >> 10) & 31
                image.setPixelColor(x, y, QColor(r * 255 // 31, g * 255 // 31, b * 255 // 31, 0 if color == 0 else 255))
        images.append(image)
    return tuple(images)


def _font(painter, size):
    font = QFont("Arial"); font.setPixelSize(size); painter.setFont(font)
    return font


def _label(painter, rect, text, size=9, color="#ffffff"):
    font = _font(painter, size)
    painter.setPen(QColor(color))
    painter.drawText(rect, Qt.AlignmentFlag.AlignCenter,
                     QFontMetrics(font).elidedText(text, Qt.TextElideMode.ElideRight, int(rect.width())))


def _new_icon(painter, x, y, kind):
    painter.save(); painter.translate(x, y)
    painter.setPen(QPen(QColor("#f9d78a"), 1.5)); painter.setBrush(QColor("#325e8a"))
    if kind == "Evolution":
        painter.drawLine(12, 16, 12, 8); painter.drawLine(4, 8, 20, 8)
        for px, py in ((9,16),(1,0),(17,0)):
            painter.drawRoundedRect(QRectF(px,py,6,6),1,1)
    elif kind == "Digi-Dex":
        painter.drawRoundedRect(QRectF(1,2,22,18),2,2); painter.drawLine(12,3,12,19)
        for py in (7,11,15):
            painter.drawLine(4,py,9,py); painter.drawLine(15,py,20,py)
    elif kind == "Digi-Bank":  # a storage box: lid, body, latch
        painter.drawRoundedRect(QRectF(2,8,20,13),2,2); painter.drawRoundedRect(QRectF(1,3,22,6),2,2)
        painter.setBrush(QColor("#f9d78a")); painter.drawRect(QRectF(10,7,4,5))
    elif kind == "Split Screen":  # two screens side by side
        painter.drawRoundedRect(QRectF(0,4,11,15),1,1); painter.drawRoundedRect(QRectF(13,4,11,15),1,1)
    elif kind == "LAN Co-op":  # two linked computers
        painter.drawRect(QRectF(1,2,9,8)); painter.drawRect(QRectF(14,14,9,8))
        painter.drawLine(5,10,5,18); painter.drawLine(5,18,14,18)
    else:
        painter.drawEllipse(QRectF(3,3,18,18))
        _label(painter,QRectF(1,2,22,22),kind[:1],12)
    painter.restore()


def _grid(painter, menu, icons):
    pitch = 50 if len(menu.rows) <= 9 else 41  # a fourth row (with Fishing) still clears the footer
    for index, name in enumerate(menu.rows):
        col, row = index % 3, index // 3
        x,y = 48 + col * 75, 42 + row * pitch
        if index == menu.selected:
            painter.setPen(QPen(QColor("#ffe1a2"), 1))
            painter.setBrush(QColor("#284b76"))
        else:
            painter.setPen(QPen(QColor("#22364b"),1)); painter.setBrush(QColor("#111e32"))
        painter.drawRoundedRect(QRectF(x,y,70,pitch-3),3,3)
        top = 4 if pitch == 50 else 1
        native = index if index < 6 else 6 if name == "Fishing" else -1
        if 0 <= native < len(icons):
            painter.drawImage(QRectF(x+23,y+top,24,24),icons[native])
        else:
            _new_icon(painter,x+23,y+top,name)
        _label(painter,QRectF(x+1,y+pitch-20,68,14),name,9)


def _egg(painter, rect):
    painter.save(); painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    shell = QPainterPath(QPointF(rect.center().x(), rect.top()+1))
    cx, w, b = rect.center().x(), rect.width()*0.36, rect.bottom()-1
    shell.cubicTo(cx+w, rect.top()+2, cx+w*1.2, b, cx, b); shell.cubicTo(cx-w*1.2, b, cx-w, rect.top()+2, cx, rect.top()+1)
    painter.setPen(QPen(QColor("#d9ba70"),0.8)); painter.setBrush(_vertical(rect,"#fff6dc","#e2c68a")); painter.drawPath(shell)
    painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(QColor("#5aa36a"))
    for dx, dy in ((-0.15,0.45),(0.12,0.65),(0.05,0.3)):
        painter.drawEllipse(QPointF(cx+dx*rect.width(), rect.top()+dy*rect.height()), 1.6, 1.6)
    painter.restore()


def _relation(painter, relation, portraits):
    nodes = {node[0]:node for node in relation["nodes"]}
    focus = relation.get("focus",relation["current"])
    selected = relation["selected"]
    previous = [nodes[a] for a,b in relation["edges"] if b == focus and a in nodes]
    following = [nodes[b] for a,b in relation["edges"] if a == focus and b in nodes]
    colors = ("#ffca20","#22be39","#00bdd8","#335cff","#d52bd9","#e12b37")
    painter.fillRect(7,39,306,157,QColor("#293e48"))
    painter.fillRect(12,53,81,137,QColor("#344f60"))
    painter.fillRect(220,47,86,143,QColor("#344f60"))
    _label(painter,QRectF(8,38,304,10),"Newborn > In-training > Rookie > Champion > Ultimate > Mega",7,"#bfd8e7")
    _label(painter,QRectF(14,52,75,10),"Previous",8,"#bdd3df")
    _label(painter,QRectF(225,49,77,10),"Next forms",8,"#bdd3df")
    if not following:
        text = "Mega: pending" if focus in nodes and nodes[focus][2] == 5 else "No next forms"
        _label(painter,QRectF(222,114,83,15),text,8,"#adc1ce")
    if not previous:
        _label(painter,QRectF(14,114,77,15),"No previous form",7,"#adc1ce")
    center = QRectF(144,105,30,32)
    positions = {}
    for group,side in ((previous,"previous"),(following,"next")):
        for index,node in enumerate(group[:6]):
            count = min(len(group),6)
            y = 112 if count == 1 else 64 + index * (106/max(1,count-1))
            x = 19 if side == "previous" else 226 + (min(index,count-1-index)*9)
            rect = QRectF(x,y,70,21)
            positions[node[0]] = rect
            color = "#f0ad24" if side == "previous" else colors[index % len(colors)]
            painter.setPen(QPen(QColor(color),1.5 if node[0] == selected else 1))
            if side == "previous":
                painter.drawLine(QPointF(rect.right(),rect.center().y()),QPointF(center.left(),center.center().y()))
            else:
                elbow = 184 + index*4
                painter.drawLine(QPointF(center.right(),center.center().y()),QPointF(elbow,center.center().y()))
                painter.drawLine(QPointF(elbow,center.center().y()),QPointF(rect.left()-5,rect.center().y()))
                painter.drawLine(QPointF(rect.left()-5,rect.center().y()),QPointF(rect.left(),rect.center().y()))
    hidden = relation.get("hidden", ())
    def box(kind,rect,focus_box=False):
        name = nodes[kind][1]
        painter.setBrush(QColor("#42616e" if kind == selected else "#304956"))
        painter.setPen(QPen(QColor("#ffe083" if kind == selected else "#6cdbb5" if kind == relation["current"] else "#96a6ac"),1.3))
        painter.drawRect(rect)
        image = None if kind in hidden else portraits.get(kind)
        if kind in hidden:
            _label(painter,QRectF(rect.x()+2,rect.y()+2,17,17),"?",11,"#b7c6cb")
        elif image is not None:
            target = QRectF(rect.x()+2,rect.y()+2,rect.width()-4,rect.height()-4) if focus_box else QRectF(rect.x()+2,rect.y()+2,17,17)
            painter.drawImage(target,image)
        else:
            _label(painter,QRectF(rect.x()+2,rect.y()+2,20,rect.height()-4),name[:2],9,"#b7c6cb")
        if not focus_box:
            _label(painter,QRectF(rect.x()+21,rect.y()+1,rect.width()-22,rect.height()-2),name,7)
    for kind,rect in positions.items():box(kind,rect)
    if focus in nodes:
        box(focus,center,True)
        _label(painter,QRectF(100,141,116,16),nodes[focus][1],10,"#ffffff")
        stage = ("","Newborn","In-training","Rookie","Champion","Ultimate")[nodes[focus][2]]
        _label(painter,QRectF(100,158,116,13),stage,9,"#b5d3df")
        _label(painter,QRectF(100,177,116,12),f"Partner level {relation['level']}",8,"#fad990")
    _label(painter,QRectF(7,195,306,14),relation['selected_name'],9,"#ffe083")


# Technique elements (digidex.ELEMENTS order): fill and glyph colours for the Dex's own vector icons.
ELEMENT_COLORS = (("#e8452c","#ffe3a0"), ("#ef8a2b","#fff1d6"), ("#e3c437","#5a3d00"), ("#4fae45","#eaffd8"),
                  ("#7fd3f0","#0f4a6e"), ("#8d9aa8","#f2f6fa"), ("#9a6a3c","#f6e2c4"))
STATUS_COLORS = {1: "#b46ae6", 2: "#f07ac8", 3: "#ffe36a", 4: "#c9b49a"}  # Poison, Confuse, Stun, Flat


def _vertical(rect, top, bottom):
    gradient = QLinearGradient(rect.topLeft(), rect.bottomLeft())
    gradient.setColorAt(0, QColor(top)); gradient.setColorAt(1, QColor(bottom))
    return QBrush(gradient)


def draw_element_icon(painter, rect, element):
    """Round badge in the element's colour with a simple glyph: flame, impact, bolt, leaf, crystal, gear, pile."""
    fill, ink = ELEMENT_COLORS[element % len(ELEMENT_COLORS)]
    painter.save()
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.translate(rect.x(), rect.y()); painter.scale(rect.width()/14, rect.height()/14)
    painter.setPen(QPen(QColor(fill).darker(170),1)); painter.setBrush(_vertical(QRectF(0,0,14,14),QColor(fill).lighter(130).name(),fill))
    painter.drawEllipse(QRectF(0.5,0.5,13,13))
    painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(QColor(ink))
    if element == 0:
        flame = QPainterPath(QPointF(7,2)); flame.cubicTo(11,6,11,9,10,10.5); flame.cubicTo(9,12.5,5,12.5,4,10.5)
        flame.cubicTo(3,8.5,5,7,6,4.5); flame.cubicTo(6.5,6,7.5,6.5,7,2); painter.drawPath(flame)
    elif element == 1:
        star = QPainterPath(QPointF(7,1.8))
        for px,py in ((8.4,5.4),(12.2,4.6),(9.6,7.6),(12,11),(8,9.6),(5.6,12.4),(5.6,8.8),(2,8.4),(5.2,6.2),(3.4,2.8),(6.4,5)):
            star.lineTo(px,py)
        star.closeSubpath(); painter.drawPath(star)
    elif element == 2:
        bolt = QPainterPath(QPointF(8.5,1.8))
        for px,py in ((4,7.8),(6.8,7.8),(5,12.2),(10.2,5.8),(7.4,5.8),(9.2,1.8)):
            bolt.lineTo(px,py)
        bolt.closeSubpath(); painter.drawPath(bolt)
    elif element == 3:
        leaf = QPainterPath(QPointF(3,11)); leaf.cubicTo(3,5,7,2.5,11.5,2.5); leaf.cubicTo(11.5,7,9,11,3,11)
        painter.drawPath(leaf)
        painter.setPen(QPen(QColor(fill).darker(150),0.8)); painter.drawLine(QPointF(3.5,10.5),QPointF(10,4))
    elif element == 4:
        painter.setPen(QPen(QColor(ink),1.3,Qt.PenStyle.SolidLine,Qt.PenCapStyle.RoundCap))
        for dx,dy in ((0,4.6),(4,2.3),(4,-2.3)):
            painter.drawLine(QPointF(7-dx,7-dy),QPointF(7+dx,7+dy))
    elif element == 5:
        for angle in range(0,360,60):
            painter.save(); painter.translate(7,7); painter.rotate(angle)
            painter.drawRect(QRectF(-1.1,-5.4,2.2,2.4)); painter.restore()
        painter.drawEllipse(QPointF(7,7),3.6,3.6)
        painter.setBrush(QColor(fill)); painter.drawEllipse(QPointF(7,7),1.4,1.4)
    else:
        for cx,cy,rx,ry in ((7,10.4,4.4,1.9),(7,8,3.3,1.7),(7,5.8,2.2,1.5)):
            painter.drawEllipse(QPointF(cx,cy),rx,ry)
        painter.drawEllipse(QPointF(7.6,3.8),1,1)
    painter.restore()


def element_icon_image(element, size=28):
    image = QImage(size,size,QImage.Format.Format_ARGB32_Premultiplied); image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image); draw_element_icon(painter,QRectF(0,0,size,size),element); painter.end()
    return image


def _dex_panel(painter, x, w):
    """Game-style window: deep blue gradient, gold double rule, a title band."""
    outer = QRectF(x,20,w,204)
    painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(_vertical(outer,"#16325a","#081326")); painter.drawRect(outer)
    painter.setBrush(_vertical(QRectF(x+2,22,w-4,15),"#3a5f8f","#1a3459")); painter.drawRect(QRectF(x+2,22,w-4,15))
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(QColor("#d9ba70"),1)); painter.drawRect(QRectF(x+1,21,w-2,202))
    painter.setPen(QPen(QColor("#6e5a2c"),1)); painter.drawRect(QRectF(x+3,23,w-6,198))
    painter.setPen(QPen(QColor("#d9ba70"),0.8)); painter.drawLine(QPointF(x+4,37),QPointF(x+w-4,37))


def _dex_row(painter, style, text, move, x, width, y, color):
    """One Dex row: headers on a gold-edged band, 'Label: value' in gold/white, skills with element icon/power/MP."""
    if style == "header":
        band = QRectF(x-4,y+1,width+6,15)
        painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(_vertical(band,"#5a4718","#2e2408")); painter.drawRoundedRect(band,2,2)
        painter.setPen(QPen(QColor("#d9ba70"),0.8)); painter.drawLine(QPointF(band.left(),band.bottom()),QPointF(band.right(),band.bottom()))
        _left(painter,QRectF(x,y,width,17),text,9,"#ffe083",bold=True)
    elif style == "skill":
        element = move["element"] if move else None
        if element is not None:
            fill = QColor(ELEMENT_COLORS[element][0]); fill.setAlpha(70)
            painter.fillRect(QRectF(x-4,y+1,width+6,15),fill)
            draw_element_icon(painter,QRectF(x-2,y+1.5,14,14),element)
        else:
            _label(painter,QRectF(x-2,y+1,14,15),"•",10,"#d9ba70")
        right = ""
        if move:
            right = f"Pow {move['power']}  MP {move['mp']}" if move["power"] else f"Support  MP {move['mp']}"
        font = _font(painter,8); right_w = QFontMetrics(font).horizontalAdvance(right) + 2 if right else 0
        if move and move["status"] in STATUS_COLORS:
            painter.setPen(Qt.PenStyle.NoPen); painter.setBrush(QColor(STATUS_COLORS[move["status"]]))
            painter.drawEllipse(QPointF(x+width-right_w-5,y+8.5),2.4,2.4); right_w += 8
        _left(painter,QRectF(x+15,y,width-17-right_w,17),text,9,"#ffffff",bold=True)
        if right:
            painter.setFont(font)  # the name above changed the font; the width was measured with this one
            painter.setPen(QColor("#bcd8f2")); painter.drawText(QRectF(x,y,width,17),Qt.AlignmentFlag.AlignRight|Qt.AlignmentFlag.AlignVCenter,right)
    elif style == "stat":
        cells = [part for part in text.split("   ")]
        cell = width/len(cells)
        for i,part in enumerate(cells):
            label,_,value = part.rpartition(" ")
            _left(painter,QRectF(x+i*cell,y,cell*0.55,17),label,8,"#f0c860")
            _left(painter,QRectF(x+i*cell+cell*0.45,y,cell*0.55,17),value,9,"#ffffff",bold=True)
    elif style == "note":
        _left(painter,QRectF(x,y,width,17),text,8,"#8fb0cf",italic=True)
    elif style == "info" and ": " in text:
        label,value = text.split(": ",1)
        font = _font(painter,9); label_w = QFontMetrics(font).horizontalAdvance(label+": ")
        _left(painter,QRectF(x,y,label_w+2,17),label+":",9,"#f0c860")
        _left(painter,QRectF(x+label_w,y,width-label_w,17),value,9,"#ffffff",bold=True)
    else:
        _left(painter,QRectF(x,y,width,17),text,10 if style == "text" else 9,color)


def _left(painter, rect, text, size, color, bold=False, italic=False):
    font = _font(painter,size); font.setBold(bold); font.setItalic(italic); painter.setFont(font)
    painter.setPen(QColor(color))
    painter.drawText(rect,Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter,
                     QFontMetrics(font).elidedText(text,Qt.TextElideMode.ElideRight,int(rect.width())))


def evolution_menu_image(image, menu, icons=(), portraits=None, avatars=None):
    """`avatars`: Digimon kind -> rendered model (avatars.py), shown in Digi-Dex lists and pages."""
    if menu is None:
        return image
    avatars = avatars or {}
    image = image.convertToFormat(QImage.Format.Format_RGB32)
    painter = QPainter(image)
    painter.scale(image.width()/320,image.height()/240)
    relation = menu.layout == "relation"
    page_avatar = avatars.get(menu.avatar) if menu.avatar is not None else None
    x,w = (3,314) if relation else (44,232) if menu.layout == "grid" else (20,280) if page_avatar else (58,204)
    if menu.layout != "list":  # the Digi-Dex (list layout) draws its own themed panel
        painter.fillRect(x,20,w,204,QColor("#0a1528"))
        painter.setPen(QColor("#d9ba70")); painter.drawRect(x+1,21,w-2,202)
        _label(painter,QRectF(x+4,22,w-8,14),menu.title,10,"#fad990")
    if relation:
        _relation(painter,menu.relation,portraits or {})
    elif menu.layout == "grid":
        _grid(painter,menu,icons)
    else:
        _dex_panel(painter,x,w)
        _label(painter,QRectF(x+4,22,w-8,14),menu.title,10,"#fad990")
        text_x, text_w = (80,212) if page_avatar else (74,176)
        if page_avatar:
            frame = QRectF(26,44,48,48)
            painter.setPen(QPen(QColor("#d9ba70"),1)); painter.setBrush(_vertical(frame,"#24456b","#0f2038"))
            painter.drawRoundedRect(frame,3,3)
            painter.drawImage(frame.adjusted(1,1,-1,-1),page_avatar)
        if menu.icons:
            text_x, text_w = 92,158
        for index,row in enumerate(menu.rows):
            y = 43 + index*18
            style = menu.styles[index] if index < len(menu.styles) else ("text" if menu.numbered else "info")
            move = menu.skills[index] if index < len(menu.skills) else None
            if index == menu.selected:
                band = QRectF(68,y,184,17)
                painter.setPen(QPen(QColor("#ffe1a2"),1)); painter.setBrush(_vertical(band,"#3b6a9c","#203f66"))
                painter.drawRoundedRect(band,2,2)
            kind = menu.icons[index] if index < len(menu.icons) else None
            if kind == "egg":
                _egg(painter,QRectF(72,y,17,17))
            elif kind == "empty":
                pass
            elif kind in avatars:
                painter.drawImage(QRectF(72,y,17,17),avatars[kind])
            elif menu.icons:
                _label(painter,QRectF(72,y,17,17),"?",10,"#7f93a3")
            text = f"{menu.offset+index+1}. {row}" if menu.numbered else row
            locked = menu.icons and kind in (None, "empty")
            _dex_row(painter,style,text,move,text_x,text_w,y,"#7f93a3" if locked else "#ffffff")
    _label(painter,QRectF(x+4,210,w-8,11),menu.footer,8,"#fad990")
    painter.end()
    return image


def native_portraits(core):
    """Original chart portraits from the user's resident UI atlas, bounded to 61 entries."""
    state = core.serialize(); marker = state.find(GPURAM_FIELD)
    if marker < 0:return {}
    start = marker + len(GPURAM_FIELD) + 4
    if struct.unpack_from("<I",state,marker+len(GPURAM_FIELD))[0] != 1024*512*2:return {}
    vram = state[start:start+1024*512*2]
    if len(vram) != 1024*512*2:return {}
    table = core.read_memory(ram_offset(0x80124544),61*8)
    cluts = struct.unpack("<5H",core.read_memory(ram_offset(0x80123E6C),10))
    images = {}
    for kind in range(1,62):
        u,v,palette = table[(kind-1)*8+4:(kind-1)*8+7]
        if palette>=5 or u>240 or v>240:continue
        clut = cluts[palette]; cx,cy = (clut&63)*16,clut>>6
        if cy>=512 or cx+16>1024:continue
        image = QImage(16,16,QImage.Format.Format_ARGB32)
        for y in range(16):
            for x in range(16):
                word = struct.unpack_from("<H",vram,((256+v+y)*1024+512+(u+x)//4)*2)[0]
                index = (word>>(((u+x)%4)*4))&15
                c = struct.unpack_from("<H",vram,((cy*1024)+cx+index)*2)[0]
                image.setPixelColor(x,y,QColor((c&31)*255//31,((c>>5)&31)*255//31,((c>>10)&31)*255//31,0 if c==0 else 255))
        images[kind] = image
    return images
