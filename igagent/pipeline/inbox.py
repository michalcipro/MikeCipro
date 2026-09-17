"""Párování natočených souborů s náměty ve frontě.

Konvence je jednoduchá: **název souboru začíná číslem námětu.**

    data/inbox/1-sebevedomi.mp4      → námět #1
    data/inbox/4_reset.mov           → námět #4
    data/inbox/7 wisconsin.mp4       → námět #7
    data/inbox/5-a.jpg, 5-b.jpg      → oba k námětu #5 (karusel)

Soubory bez čísla zůstanou nepřiřazené — agent si je sám k ničemu nepřipne,
protože by jinak přilepil stejné video ke třem různým námětům. Můžeš je
přiřadit ručně (`igagent queue attach`) nebo nechat doplnit podle pořadí
(`igagent inbox link --auto`).

Použitý soubor se po výrobě odsune do `data/inbox/hotovo/`, aby se
nepoužil podruhé.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from ..util import get_logger

log = get_logger(__name__)

VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v", ".avi", ".mkv"}
PHOTO_SUFFIXES = {".jpg", ".jpeg", ".png", ".heic", ".webp"}
MEDIA_SUFFIXES = VIDEO_SUFFIXES | PHOTO_SUFFIXES

ARCHIVE_DIR = "hotovo"
_PREFIX_RE = re.compile(r"^(\d{1,5})\s*[-_. ]")


class Inbox:
    def __init__(self, settings, store):
        self.settings = settings
        self.store = store
        self.path = Path(settings.data_dir) / "inbox"
        self.archive_path = self.path / ARCHIVE_DIR

    # ------------------------------------------------------------ čtení
    def ensure(self):
        self.path.mkdir(parents=True, exist_ok=True)
        return self.path

    def files(self):
        """Média ve složce inbox (bez archivu)."""
        if not self.path.exists():
            return []
        return sorted(p for p in self.path.iterdir()
                      if p.is_file() and p.suffix.lower() in MEDIA_SUFFIXES)

    def attached_paths(self):
        """Soubory, které už k nějakému námětu patří — ty se znovu nenabízejí."""
        used = set()
        for item in self.store.queue(limit=500):
            for path in item.source_media or []:
                used.add(str(Path(path).resolve()))
        return used

    @staticmethod
    def item_id_from_name(path):
        """Číslo námětu z názvu souboru, nebo None."""
        match = _PREFIX_RE.match(Path(path).name)
        return int(match.group(1)) if match else None

    # ------------------------------------------------------------ párování
    def plan_matches(self, auto=False):
        """Co kam patří. Vrací (dvojice, nepřiřazené soubory, čekající náměty)."""
        used = self.attached_paths()
        available = [p for p in self.files() if str(p.resolve()) not in used]

        waiting = [item for item in self.store.queue(status=("planned", "failed"), limit=200)
                   if (item.brief or {}).get("needs_user_media") and not item.source_media]
        waiting.sort(key=lambda i: (i.scheduled_for or "9999", i.id))
        waiting_ids = {item.id: item for item in waiting}

        matches, leftover = {}, []
        for path in available:
            item_id = self.item_id_from_name(path)
            if item_id is not None and item_id in waiting_ids:
                matches.setdefault(item_id, []).append(path)
            elif item_id is not None:
                # číslo sedí na položku, která média nepotřebuje nebo už je má
                existing = self.store.get_queue_item(item_id)
                if existing:
                    matches.setdefault(item_id, []).append(path)
                else:
                    leftover.append(path)
            else:
                leftover.append(path)

        if auto and leftover:
            # doplň podle pořadí: nejbližší termín dostane první soubor
            free = [item for item in waiting if item.id not in matches]
            for item, path in zip(free, list(leftover)):
                matches.setdefault(item.id, []).append(path)
                leftover.remove(path)

        unmatched_items = [item for item in waiting if item.id not in matches]
        return matches, leftover, unmatched_items

    # ------------------------------------------------------------ zápis
    def link(self, auto=False, dry_run=False):
        """Připne soubory k námětům podle `plan_matches`."""
        matches, leftover, waiting = self.plan_matches(auto=auto)
        linked = []
        for item_id, paths in sorted(matches.items()):
            item = self.store.get_queue_item(item_id)
            if item is None:
                continue
            new_paths = [str(p.resolve()) for p in paths]
            if dry_run:
                linked.append((item, new_paths))
                continue
            item.source_media = list(item.source_media or []) + new_paths
            if item.status == "failed":
                item.status = "planned"      # chyběl materiál, teď je
                item.error = None
            self.store.update_queue(item)
            linked.append((item, new_paths))
            log.info("#%s ← %s", item.id, ", ".join(Path(p).name for p in new_paths))
        return linked, leftover, waiting

    def archive(self, paths):
        """Odsune použité soubory do `hotovo/`, ať se nepoužijí znovu."""
        self.archive_path.mkdir(parents=True, exist_ok=True)
        moved = []
        for raw in paths:
            source = Path(raw)
            if not source.exists() or source.parent.resolve() != self.path.resolve():
                continue          # soubor odjinud než z inboxu neuklízíme
            target = self.archive_path / source.name
            counter = 1
            while target.exists():
                target = self.archive_path / f"{source.stem}-{counter}{source.suffix}"
                counter += 1
            shutil.move(str(source), str(target))
            moved.append(target)
            log.info("Uklizeno: %s → %s/", source.name, ARCHIVE_DIR)
        return moved

    # ------------------------------------------------------------ přehled
    def status(self):
        matches, leftover, waiting = self.plan_matches()
        return {
            "slozka": str(self.path),
            "souboru": len(self.files()),
            "prirazeno": {item_id: [p.name for p in paths]
                          for item_id, paths in sorted(matches.items())},
            "bez_cisla": [p.name for p in leftover],
            "cekaji_na_video": [(item.id, item.title) for item in waiting],
        }
