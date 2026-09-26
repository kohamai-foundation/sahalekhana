"""Anchor the co-writing record: fold every project's HEAD into one root hash.

    python manage.py anchor_lekhana          # record the root locally
    python manage.py anchor_lekhana --ots    # also stamp it with OpenTimestamps

The root and the (project, HEAD) list go to ``<LEKHANA_REPO_ROOT>/_anchors/`` and
to a ``lekhana.Anchor`` row. ``--ots`` runs the ``ots`` client
(``pip install opentimestamps-client``), which sends ONLY the file's hash to the
public calendar servers; ``ots upgrade <file>.ots`` later completes the proof.
Nothing is recorded when no HEAD moved since the last anchor.
"""
import shutil
import subprocess
from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand, CommandError
from django.utils import timezone

from lekhana import ledger
from lekhana.models import Anchor, Project


class Command(BaseCommand):
    help = "Fold every project's HEAD into one root hash; optionally stamp it with OpenTimestamps."

    def add_arguments(self, parser):
        parser.add_argument("--ots", action="store_true",
                            help="Also stamp the anchor file with OpenTimestamps (sends only its hash).")

    def handle(self, *args, ots=False, **options):
        heads = []
        for project in Project.objects.order_by("slug"):
            sha = ledger.head(ledger.repo_path(project.slug))
            if sha:
                heads.append([project.slug, sha])
        if not heads:
            self.stdout.write("No project has a commit yet. Nothing to anchor.")
            return
        root = ledger.anchor_root([tuple(h) for h in heads])
        last = Anchor.objects.first()
        if last and last.root_hash == root:
            self.stdout.write(f"No HEAD moved since the anchor of {last.created_at:%Y-%m-%d %H:%M}. Nothing recorded.")
            return

        folder = Path(settings.LEKHANA_REPO_ROOT) / "_anchors"
        folder.mkdir(parents=True, exist_ok=True)
        stamp = timezone.now().strftime("%Y%m%dT%H%M%SZ")
        record = folder / f"{stamp}.txt"
        record.write_text("".join(f"{slug} {sha}\n" for slug, sha in heads) + f"root {root}\n", encoding="utf-8")

        log, proof = "local", str(record)
        if ots:
            client = shutil.which("ots")
            if client is None:
                raise CommandError("The OpenTimestamps client isn't installed (pip install opentimestamps-client). "
                                   f"The local record was written to {record}; run the command again with --ots once it is.")
            try:
                subprocess.run([client, "stamp", str(record)], check=True, capture_output=True, text=True)
            except subprocess.CalledProcessError as exc:
                raise CommandError(f"ots stamp failed: {(exc.stderr or exc).strip()}") from exc
            log, proof = "opentimestamps", f"{record}.ots"

        Anchor.objects.create(root_hash=root, heads=heads, log=log, proof=proof)
        self.stdout.write(self.style.SUCCESS(f"Anchored {len(heads)} project(s): root {root} ({log})."))
