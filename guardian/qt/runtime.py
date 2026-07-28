"""Read-only runtime context for the Stage 3 Qt shell."""

from __future__ import annotations

import time

from ..config import StationConfig
from ..install import (
    DependencyKind,
    DependencyStatus,
    hamlib_installer,
    vara_installer,
)
from ..install.dependencies import inspect_dependencies
from ..i18n import dual
from ..message import Folder, MessageStore
from ..operations import Operations
from ..routing import HeardStations, RouteTable
from ..updates import UpdateInfo, check_for_update, download_installer
from ..services import (
    DependencySnapshot,
    EventBus,
    MailboxSnapshot,
    NetworkSnapshot,
    SnapshotStore,
    TaskResult,
    WorkerPool,
)


class ShellRuntime:
    """Publishes current local state without owning radio/protocol objects."""

    def __init__(self) -> None:
        self.config = StationConfig.load()
        self.events = EventBus(history_limit=2_000)
        self.snapshots = SnapshotStore()
        self.workers = WorkerPool(max_workers=3, thread_name_prefix="guardian-qt")
        self.dependency_statuses: tuple[DependencyStatus, ...] = ()
        self.mailstore = MessageStore()
        self.routes = RouteTable.load()
        self.heard = HeardStations()
        self.operations = Operations(
            self.config,
            self.events,
            self.snapshots,
            self.workers,
            self.mailstore,
            self.routes,
            self.heard,
        )
        self.refresh()
        self.request_dependency_refresh()
        self.events.publish(
            dual("Guardian Monitor shell started.", "Guardian Monitor byl spuštěn."),
            source="ui",
        )

    def refresh(self) -> None:
        counts = self.mailstore.counts()
        current_network = self.snapshots.read().network
        self.snapshots.update(
            mailbox=MailboxSnapshot(
                inbox=counts.get(Folder.INBOX, 0),
                unread=self.mailstore.unread(Folder.INBOX),
                outbox=counts.get(Folder.OUTBOX, 0),
                outbox_failed=self.mailstore.failed(Folder.OUTBOX),
                transit=counts.get(Folder.TRANSIT, 0),
            ),
            network=NetworkSnapshot(
                active_sessions=current_network.active_sessions,
                heard_stations=len(self.heard.active(time.monotonic())),
                control_channel_active=current_network.control_channel_active,
                scanner_active=current_network.scanner_active,
            ),
        )
        self._refreshed_at = time.monotonic()

    def drain_workers(self) -> None:
        self.workers.drain()

    def tick(self) -> None:
        self.operations.tick()

    def request_dependency_refresh(self) -> bool:
        config = self.config

        def completed(result: TaskResult) -> None:
            if result.error is not None:
                self.events.publish(
                    dual(
                        f"Dependency scan failed: {result.error}",
                        f"Kontrola závislostí selhala: {result.error}",
                    ),
                    source="dependency",
                )
                return
            self.dependency_statuses = tuple(result.value)
            by_kind = {item.kind: item for item in self.dependency_statuses}
            hamlib = by_kind[DependencyKind.HAMLIB]
            vara_fm = by_kind[DependencyKind.VARA_FM]
            vara_hf = by_kind[DependencyKind.VARA_HF]
            self.snapshots.update(
                dependencies=DependencySnapshot(
                    hamlib_available=hamlib.available,
                    hamlib_path=hamlib.executable,
                    vara_fm_available=vara_fm.available,
                    vara_hf_available=vara_hf.available,
                )
            )
            self.events.publish(
                dual(
                    "Dependency scan complete.",
                    "Kontrola závislostí byla dokončena.",
                ),
                source="dependency",
            )

        return self.workers.submit(
            "dependency-scan",
            lambda: inspect_dependencies(config),
            completed,
        )

    def install_hamlib(self, on_complete=None) -> bool:
        self.events.publish(
            dual(
                "Installing verified Hamlib package…",
                "Instaluji ověřený balíček Hamlib…",
            ),
            source="dependency",
        )

        def install():
            return hamlib_installer.install(
                progress=lambda message: self.events.publish(
                    message, source="dependency"
                )
            )

        def completed(result: TaskResult) -> None:
            if result.error is not None:
                self.events.publish(
                    dual(
                        f"Hamlib installation failed: {result.error}",
                        f"Instalace Hamlibu selhala: {result.error}",
                    ),
                    source="dependency",
                )
            else:
                self.config.rigctld_path = str(result.value)
                self.config.save()
                self.events.publish(
                    dual(
                        "Hamlib installation completed.",
                        "Instalace Hamlibu byla dokončena.",
                    ),
                    source="dependency",
                )
                self.request_dependency_refresh()
            if on_complete is not None:
                on_complete(result)

        return self.workers.submit("hamlib-install", install, completed)

    def request_update_check(self, on_complete=None) -> bool:
        def completed(result: TaskResult) -> None:
            if result.error is not None:
                self.events.publish(
                    dual(
                        f"Update check failed: {result.error}",
                        f"Kontrola aktualizace selhala: {result.error}",
                    ),
                    source="update",
                )
            elif result.value is None:
                self.events.publish(
                    dual(
                        "Guardian is up to date.",
                        "Guardian je aktuální.",
                    ),
                    source="update",
                )
            else:
                self.events.publish(
                    dual(
                        f"Guardian {result.value.version} is available.",
                        f"Je dostupný Guardian {result.value.version}.",
                    ),
                    source="update",
                )
            if on_complete is not None:
                on_complete(result)

        return self.workers.submit(
            "update-check",
            check_for_update,
            completed,
        )

    def download_vara(
        self,
        kind: DependencyKind,
        destination,
        on_complete=None,
    ) -> bool:
        package = vara_installer.package_for(kind)
        task_name = f"vara-download-{kind.value}"
        if self.workers.is_active(task_name):
            return False
        self.events.publish(
            dual(
                f"Downloading verified {package.product} {package.version}…",
                f"Stahuji ověřený {package.product} {package.version}…",
            ),
            source="dependency",
        )
        last_percent = -10

        def progress(received: int, total: int) -> None:
            nonlocal last_percent
            percent = received * 100 // total
            if percent < last_percent + 10 and percent != 100:
                return
            last_percent = percent
            self.events.publish(
                dual(
                    f"{package.product} download: {percent}%",
                    f"Stahování {package.product}: {percent} %",
                ),
                source="dependency",
            )

        def completed(result: TaskResult) -> None:
            if result.error is not None:
                self.events.publish(
                    dual(
                        f"{package.product} download failed: {result.error}",
                        f"Stažení {package.product} selhalo: {result.error}",
                    ),
                    source="dependency",
                )
            else:
                self.events.publish(
                    dual(
                        f"Verified {package.product} installer is ready.",
                        f"Ověřený instalátor {package.product} je připraven.",
                    ),
                    source="dependency",
                )
            if on_complete is not None:
                on_complete(result)

        return self.workers.submit(
            task_name,
            lambda: vara_installer.download_and_extract(
                kind,
                destination,
                progress=progress,
            ),
            completed,
        )

    def download_update(
        self,
        info: UpdateInfo,
        on_complete=None,
        progress=None,
    ) -> bool:
        return self.workers.submit(
            "update-download",
            lambda: download_installer(info, progress=progress),
            on_complete,
        )

    def close(self) -> None:
        self.operations.close()
        self.workers.close(wait=False)
