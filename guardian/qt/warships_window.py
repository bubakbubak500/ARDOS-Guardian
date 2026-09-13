"""Hidden, nonmodal radio duel UI. Rendering never advances the protocol."""
import time

from PySide6.QtCore import Qt, QTimer, QEvent, QObject, QVariantAnimation, QMimeData
from PySide6.QtGui import QColor, QBrush, QShortcut, QKeySequence, QDrag, QPainter, QPen
from PySide6.QtWidgets import (QApplication, QWidget, QVBoxLayout, QHBoxLayout,
    QLabel, QPushButton, QComboBox, QTableWidget, QTableWidgetItem, QHeaderView,
    QMessageBox, QCheckBox, QProgressBar, QDialog, QLineEdit, QTextEdit,
    QPlainTextEdit, QAbstractSpinBox, QAbstractItemView)

from ..protocol.warships import Op, ship_cells
from ..warships.service import WarshipsService
from ..warships.model import Game
from .window_geometry import fit_window_to_screen
from .theme import LIGHT_TOKENS


STATES = {
    "LOBBY": "Vyber přímého souseda.", "INVITE_SENT": "Čeká na přijetí výzvy…",
    "INVITE_RECEIVED": "Přišla výzva na lodě.", "PLACING": "Rozmísti flotilu.",
    "READY_WAIT": "Čeká na připravenost soupeře…", "MY_TURN": "Na tahu jsi ty",
    "PEER_TURN": "Na tahu je soupeř", "SHOT_QUEUED": "Čeká na volný kanál",
    "WAIT_RESULT": "Čeká na výsledek", "WAIT_RESULT_ACK": "Čeká na potvrzení výsledku",
    "ACK_QUEUED": "Potvrzuji výsledek", "PAUSED": "Spojení přerušeno",
    "WON": "Moře je tvoje.", "LOST": "Dnes vyhrál oceán.",
    "ENDING": "Čeká na potvrzení ukončení…", "CANCELLED": "Hra ukončena bez vítěze.",
    "DECLINED": "Výzva skončila.",
}


class Sea(QTableWidget):
    def __init__(self, owner, local):
        super().__init__(10, 10)
        self.owner, self.local = owner, local
        self.setAccessibleName("Moje flotila" if local else "Soupeřovo moře")
        self.setHorizontalHeaderLabels(list("ABCDEFGHIJ"))
        self.setVerticalHeaderLabels([str(i) for i in range(1, 11)])
        self.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.verticalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
        self.setMinimumSize(300, 300)
        self.setStyleSheet("QTableWidget { gridline-color: #72939a; } QTableWidget::item { border: 0px; }")
        for cell in range(100):
            item = QTableWidgetItem("")
            item.setTextAlignment(Qt.AlignmentFlag.AlignCenter)
            self.setItem(cell // 10, cell % 10, item)
        self.cellClicked.connect(self.clicked_cell)
        self.setDragEnabled(local)
        self.setAcceptDrops(local)
        self.effect_cell = None
        self.effect_op = None
        self.effect = QVariantAnimation(self)
        self.effect.setStartValue(0.0)
        self.effect.setEndValue(1.0)
        self.effect.valueChanged.connect(lambda _: self.viewport().update())

    def pulse(self, cell, op):
        if self.owner.reduced.isChecked() or not self.owner.isVisible() or self.owner.isMinimized():
            return
        self.effect_cell, self.effect_op = cell, op
        self.effect.stop()
        self.effect.setDuration({Op.MISS: 350, Op.HIT: 180, Op.SUNK: 650, Op.LOSS: 650, Op.SHOT: 300}.get(op, 120))
        self.effect.start()

    def paintEvent(self, event):
        super().paintEvent(event)
        if self.effect_cell is None or self.effect.state() != QVariantAnimation.State.Running:
            return
        rect = self.visualItemRect(self.item(self.effect_cell // 10, self.effect_cell % 10))
        progress = float(self.effect.currentValue() or 0)
        painter = QPainter(self.viewport())
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        color = QColor(self.owner.tokens.warning if self.effect_op in {Op.HIT, Op.SUNK, Op.LOSS} else self.owner.tokens.accent)
        color.setAlpha(int(200 * (1 - progress)))
        painter.setPen(QPen(color, 2))
        if self.effect_op in {Op.HIT, Op.SUNK, Op.LOSS}:
            painter.fillRect(rect.adjusted(2, 2, -2, -2), color)
        else:
            radius = min(rect.width(), rect.height()) * (.15 + .45 * progress)
            painter.drawEllipse(rect.center(), radius, radius)
        painter.end()

    def clicked_cell(self, row, column):
        if self.local:
            self.owner.place(row * 10 + column)
        else:
            self.owner.target = row * 10 + column
            self.owner.refresh()

    def keyPressEvent(self, event):
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter):
            if self.local:
                self.owner.place(self.currentRow() * 10 + self.currentColumn())
            else:
                self.owner.target = self.currentRow() * 10 + self.currentColumn()
                self.owner.action(self.owner.fire)
            return
        super().keyPressEvent(event)

    def startDrag(self, supported):
        game = self.owner.service.game
        if not self.local or game.state != "PLACING":
            return
        cell = self.currentRow() * 10 + self.currentColumn()
        for index, ship in enumerate(game.board.ships):
            if cell in ship_cells(*ship):
                mime = QMimeData()
                mime.setData("application/x-guardian-ship", str(index).encode())
                drag = QDrag(self)
                drag.setMimeData(mime)
                drag.exec(Qt.DropAction.MoveAction)
                break

    def dragEnterEvent(self, event):
        if event.source() is self and self.owner.service.game.state == "PLACING":
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def dropEvent(self, event):
        if event.source() is not self or self.owner.service.game.state != "PLACING":
            return
        index = self.indexAt(event.position().toPoint())
        if not index.isValid():
            return
        board = self.owner.service.game.board
        original = list(board.ships)
        ship = board.ships.pop(int(bytes(event.mimeData().data("application/x-guardian-ship"))))
        try:
            board.place(index.row() * 10 + index.column(), ship[1] & 127, bool(ship[1] & 128))
            event.acceptProposedAction()
        except ValueError as exc:
            board.ships = original
            self.owner.detail.setText(str(exc))
        self.owner.refresh()


class WarshipsWindow(QWidget):
    def __init__(self, service, parent=None):
        super().__init__(parent, Qt.WindowType.Window)
        self.service = service
        self.tokens = LIGHT_TOKENS
        self.target = None
        self.previous = None
        self.last_enemy = {}
        self.last_hits = set()
        self.setWindowTitle("Guardian Warships")
        self.resize(840, 650)
        layout = QVBoxLayout(self)
        self.title = QLabel("Guardian Warships · Přímý spoj")
        layout.addWidget(self.title)
        self.sweep = QProgressBar()
        self.sweep.setRange(0, 100)
        self.sweep.setTextVisible(False)
        self.sweep.setFixedHeight(3)
        layout.addWidget(self.sweep)
        self.animation = QVariantAnimation(self)
        self.animation.setStartValue(0)
        self.animation.setEndValue(100)
        self.animation.valueChanged.connect(self.sweep.setValue)
        self.animation.finished.connect(lambda: self.sweep.setValue(0))
        lobby = QHBoxLayout()
        self.peers = QComboBox()
        self.peers.setMinimumContentsLength(22)
        lobby.addWidget(self.peers, 1)
        self.invite_button = self.button(lobby, "Vyzvat souseda", lambda: service.invite(self.peers.currentData()))
        self.new_button = self.button(lobby, "Zpět do lobby", self.new_game)
        layout.addLayout(lobby)
        self.state_label = QLabel()
        layout.addWidget(self.state_label)
        placement = QHBoxLayout()
        self.length = QComboBox()
        for length in (5, 4, 3, 2):
            self.length.addItem(f"Loď délky {length}", length)
        placement.addWidget(self.length)
        self.vertical = QCheckBox("Svisle (R)")
        placement.addWidget(self.vertical)
        self.rotate = QShortcut(QKeySequence("R"), self)
        self.rotate.activated.connect(self.vertical.toggle)
        self.random_button = self.button(placement, "Rozmístit náhodně", lambda: service.game.board.randomize())
        self.clear_button = self.button(placement, "Vymazat lodě", lambda: service.game.board.ships.clear())
        self.ready_button = self.button(placement, "Připraven", lambda: service.game.ready())
        layout.addLayout(placement)
        seas = QHBoxLayout()
        self.mine, self.enemy = Sea(self, True), Sea(self, False)
        for label, sea in (("Moje flotila", self.mine), ("Soupeřovo moře", self.enemy)):
            column = QVBoxLayout()
            column.addWidget(QLabel(label))
            column.addWidget(sea)
            seas.addLayout(column)
        layout.addLayout(seas, 1)
        self.detail = QLabel("Kliknutím umísti loď. Tažením ji přesuň. Enter potvrdí vybrané pole.")
        self.detail.setWordWrap(True)
        layout.addWidget(self.detail)
        actions = QHBoxLayout()
        self.fire_button = self.button(actions, "Vyber cíl", self.fire)
        self.retry_button = self.button(actions, "Zkusit znovu", service.resume)
        self.resign_button = self.button(actions, "Vzdát se", self.resign)
        self.reduced = QCheckBox("Omezit pohyb")
        actions.addWidget(self.reduced)
        layout.addLayout(actions)
        self.timer = QTimer(self)
        self.timer.setInterval(200)
        self.timer.timeout.connect(self.refresh)
        self.timer.start()
        self.refresh()
        fit_window_to_screen(self, preferred_size=self.size(), minimum_size=self.minimumSizeHint())

    def button(self, layout, text, callback):
        button = QPushButton(text)
        button.clicked.connect(lambda: self.action(callback))
        layout.addWidget(button)
        return button

    def action(self, callback):
        try:
            callback()
        except (ValueError, RuntimeError) as exc:
            self.detail.setText(str(exc))
        self.refresh()

    def place(self, cell):
        if self.service.game.state == "PLACING":
            self.action(lambda: self.service.game.board.place(cell, self.length.currentData(), self.vertical.isChecked()))
            self.mine.pulse(cell, None)

    def fire(self):
        if self.target is not None:
            self.service.game.fire(self.target)

    def resign(self):
        if QMessageBox.question(self, "Vzdát se", "Opravdu se vzdát této hry?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            self.service.game.end()

    def new_game(self):
        self.service.archive_game()
        self.target = None

    def refresh(self):
        g = self.service.game
        self.title.setText(f"Guardian Warships · {g.callsign} ↔ {g.peer or '—'} · Přímý spoj")
        selected = self.peers.currentData()
        self.peers.clear()
        for call, (heard, _, snr) in sorted(self.service.neighbors().items()):
            quality = f" · {snr:.0f} dB" if snr is not None else ""
            self.peers.addItem(f"{call} · {int(time.monotonic() - heard)} s{quality}", call)
        self.peers.setCurrentIndex(max(0, self.peers.findData(selected)))
        self.invite_button.setEnabled(g.state == "LOBBY" and self.peers.count() > 0)
        self.new_button.setVisible(g.state in {"WON", "LOST", "CANCELLED", "DECLINED"} and not g.pending)
        self.state_label.setText(STATES.get(g.state, g.state))
        if not self.service.available and g.pending and g.state != "PAUSED":
            self.state_label.setText("Čeká na volný kanál")
        if g.state in {"PAUSED", "DECLINED"}:
            self.detail.setText(g.message)
        placing = g.state == "PLACING"
        for widget in (self.length, self.vertical, self.random_button, self.clear_button):
            widget.setEnabled(placing)
        self.ready_button.setEnabled(placing and g.board.valid)
        self.retry_button.setVisible(g.state == "PAUSED")
        self.resign_button.setEnabled(g.state not in {"LOBBY", "WON", "LOST", "CANCELLED", "DECLINED", "ENDING"})
        self.fire_button.setEnabled(g.state == "MY_TURN" and self.target is not None and self.target not in g.enemy)
        self.fire_button.setText(f"Vystřelit na {chr(65 + self.target % 10)}{self.target // 10 + 1}" if self.target is not None else "Vyber cíl")
        own = {c for s in g.board.ships for c in ship_cells(*s)}
        sunk = {c for s in g.enemy_ships for c in ship_cells(*s)}
        for cell in range(100):
            left, right = self.mine.item(cell // 10, cell % 10), self.enemy.item(cell // 10, cell % 10)
            left.setText(("×" if cell in own else "○") if cell in g.board.hits else ("■" if cell in own else ""))
            result = g.enemy.get(cell)
            right.setText("▣" if cell in sunk else "○" if result == Op.MISS else "×" if result is not None else "◎" if cell == getattr(g, "target", None) and g.state in {"SHOT_QUEUED", "WAIT_RESULT"} else "")
            for item, text in ((left, left.text()), (right, right.text())):
                item.setForeground(QBrush(QColor(self.tokens.warning if text in {"×", "▣"} else self.tokens.accent)))
                item.setToolTip(f"{chr(65 + cell % 10)}{cell // 10 + 1}: {text or 'neprozkoumáno'}")
        marker = (g.session, g.changed, g.state)
        for cell, result in g.enemy.items():
            if cell not in self.last_enemy:
                self.enemy.pulse(cell, result)
                self.detail.setText(f"{chr(65 + cell % 10)}{cell // 10 + 1}: " +
                                    {Op.MISS: "MIMO", Op.HIT: "ZÁSAH", Op.SUNK: "POTOPENO", Op.LOSS: "PROHRA soupeře"}[result])
        for cell in g.board.hits - self.last_hits:
            result = g.responses.get(g.turn)
            self.mine.pulse(cell, result.op if result else Op.HIT)
        self.last_enemy, self.last_hits = dict(g.enemy), set(g.board.hits)
        if marker != self.previous:
            if g.state == "WAIT_RESULT":
                self.enemy.pulse(g.target, Op.SHOT)
            if g.state == "PLACING":
                self.animate(220)
            if self.previous is not None and g.state in {"ACK_QUEUED", "WAIT_RESULT_ACK", "WON", "LOST", "WAIT_RESULT"}:
                self.animate(900 if g.state == "WON" else 350)
            self.previous = marker

    def animate(self, duration):
        if self.reduced.isChecked() or not self.isVisible() or self.isMinimized():
            return
        self.animation.stop()
        self.animation.setDuration(duration)
        self.animation.start()

    def set_tokens(self, tokens):
        self.tokens = tokens
        for sea in (self.mine, self.enemy):
            sea.setStyleSheet(f"QTableWidget {{ gridline-color: {tokens.control_border}; }} QTableWidget::item {{ border: 0px; }}")
        self.refresh()

    def showEvent(self, event):
        super().showEvent(event)
        self.animate(250)

    def changeEvent(self, event):
        if event.type() == QEvent.Type.WindowStateChange and self.isMinimized():
            self.animation.stop()
            self.mine.effect.stop()
            self.enemy.effect.stop()
        super().changeEvent(event)

    def hideEvent(self, event):
        self.animation.stop()
        self.mine.effect.stop()
        self.enemy.effect.stop()
        super().hideEvent(event)


class WarshipsAccess(QObject):
    def __init__(self, shell):
        super().__init__(shell)
        self.shell = shell
        if not hasattr(shell.runtime, "warships"):
            shell.runtime.warships = WarshipsService(shell.runtime.operations)
        self.service = shell.runtime.warships
        self.service.bind()
        self.window = None
        self.prompt = None
        self.prompt_session = None
        self.code = ""
        QApplication.instance().installEventFilter(self)
        self.timer = QTimer(self)
        self.timer.timeout.connect(self.check_invite)
        self.timer.start(250)

    def eventFilter(self, obj, event):
        if event.type() == QEvent.Type.WindowDeactivate and obj is self.shell:
            self.code = ""
        if event.type() == QEvent.Type.KeyPress:
            focus = QApplication.focusWidget()
            # Tables, navigation and the read-only activity log are ordinary
            # places to type the code. Exclude actual text entry, including an
            # editor temporarily opened inside a table.
            editing = isinstance(focus, (QLineEdit, QAbstractSpinBox))
            editing = editing or isinstance(focus, (QTextEdit, QPlainTextEdit)) and not focus.isReadOnly()
            editing = editing or isinstance(focus, QComboBox) and focus.isEditable()
            if QApplication.activeWindow() is not self.shell or editing:
                self.code = ""
                return False
            if event.isAutoRepeat():
                return bool(self.code)
            if event.modifiers() & (Qt.KeyboardModifier.ControlModifier | Qt.KeyboardModifier.AltModifier | Qt.KeyboardModifier.MetaModifier):
                self.code = ""
                return False
            self.code += event.text().lower()
            while self.code and not "idkfa".startswith(self.code):
                self.code = self.code[1:]
            if self.code == "idkfa":
                self.code = ""
                self.open()
                return True
            # Consume recognised prefix keys once. An ignored child key event
            # otherwise propagates through several parents and repeats letters.
            return bool(self.code)
        return False

    def open(self):
        if self.window is None:
            self.window = WarshipsWindow(self.service, self.shell)
            self.window.set_tokens(self.shell.theme_controller.tokens)
            self.shell.theme_controller.theme_changed.connect(self.window.set_tokens)
        self.window.show()
        self.window.raise_()
        self.window.activateWindow()

    def check_invite(self):
        game = self.service.game
        if self.prompt is not None and game.state != "INVITE_RECEIVED":
            self.prompt.close()
            self.prompt = None
        if game.state != "INVITE_RECEIVED" or self.prompt_session == (game.peer, game.session):
            return
        self.prompt_session = (game.peer, game.session)
        dialog = QDialog(self.shell)
        dialog.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
        dialog.setWindowTitle("Guardian")
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel(f"{game.peer} tě vyzývá na lodě"))
        row = QHBoxLayout()
        accept, decline = QPushButton("Přijmout"), QPushButton("Odmítnout")
        row.addWidget(accept)
        row.addWidget(decline)
        layout.addLayout(row)
        def accepted():
            game.accept()
            dialog.close()
            self.open()
        accept.clicked.connect(accepted)
        decline.clicked.connect(lambda: (game.decline(), dialog.close()))
        dialog.show()
        self.prompt = dialog

    def shutdown(self):
        self.timer.stop()
        QApplication.instance().removeEventFilter(self)
        if self.window:
            self.window.close()
        if self.prompt:
            self.prompt.close()
