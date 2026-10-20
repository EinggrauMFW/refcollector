"""Request draft renderer."""

from __future__ import annotations

from collections.abc import Sequence
from email import policy
from email.message import EmailMessage

from refcollector.contracts.ports import ExportRow, RenderedFile, Sender
from refcollector.domain.enums import DraftLanguage, Selection
from refcollector.errors import InvalidInput

from .jsonout import metadata, selected

TEMPLATES = {
    DraftLanguage.EN: {
        "greeting": "Dear authors,",
        "subject": "Request for a copy: ",
        "introduction": "My name is {name}{affiliation}.",
        "works": "I am writing about the following work(s):",
        "availability": "I could not find an eligible copy in the sources I checked.",
        "request": "Could you share a copy for personal study?",
        "purpose": "Purpose: {purpose}",
        "grouping": (
            "Grouping assumption: the first listed author is used because the corresponding author "
            "is not known in P0."
        ),
        "closing": "Kind regards,",
    },
    DraftLanguage.ID: {
        "greeting": "Yth. Bapak/Ibu penulis,",
        "subject": "Permohonan salinan: ",
        "introduction": "Nama saya {name}{affiliation}.",
        "works": "Saya menulis mengenai karya berikut:",
        "availability": (
            "Saya tidak dapat menemukan salinan yang memenuhi syarat di sumber yang saya periksa."
        ),
        "request": "Apakah Bapak/Ibu dapat membagikan salinan untuk studi pribadi?",
        "purpose": "Tujuan: {purpose}",
        "grouping": (
            "Asumsi pengelompokan: penulis pertama digunakan karena penulis korespondensi "
            "belum diketahui pada P0."
        ),
        "closing": "Hormat saya,",
    },
}


def _work_description(meta: dict) -> str:
    parts = [meta.get("title") or "[Untitled work]"]
    for field in ("year", "venue"):
        if meta.get(field):
            parts.append(str(meta[field]))
    if meta.get("doi"):
        parts.append(f"DOI: {meta['doi']}")
    if meta.get("url"):
        parts.append(f"URL: {meta['url']}")
    return "; ".join(parts)


class DefaultDraftRenderer:
    def render(
        self,
        rows: Sequence[ExportRow],
        *,
        sender: Sender,
        language: str,
        group_by_author: bool,
    ) -> list[RenderedFile]:
        try:
            template = TEMPLATES[DraftLanguage(language)]
        except ValueError as exc:
            raise InvalidInput(f"Unknown draft language: {language}") from exc
        # Access preconditions belong to draft_requests (11.4), not silent renderer filtering.
        # One work may occur in several sources; each work gets only one request.
        works = {}
        for row in selected(rows, Selection.ACCEPTED):
            if row.work is not None and row.work.work_id not in works:
                works[row.work.work_id] = metadata(row)
        groups: dict[tuple, list[dict]] = {}
        for work_id, meta in works.items():
            authors = meta.get("authors") or []
            if group_by_author and authors and any(authors[0].values()):
                author = authors[0]
                key = ("author", author.get("literal"), author.get("family"), author.get("given"))
            else:
                key = ("work", work_id)
            groups.setdefault(key, []).append(meta)
        bundle = []
        messages = []
        for number, group in enumerate(groups.values(), 1):
            subject = template["subject"] + "; ".join(
                meta.get("title") or "[Untitled work]" for meta in group
            )
            subject = " ".join(subject.splitlines())
            affiliation = f", {sender.affiliation}" if sender.affiliation else ""
            body = [
                sender.honorific_style or template["greeting"],
                "",
                template["introduction"].format(name=sender.name, affiliation=affiliation),
                "",
                template["works"],
                *("- " + _work_description(meta) for meta in group),
                "",
                template["availability"],
                "",
                template["request"],
            ]
            if sender.purpose:
                body.extend(["", template["purpose"].format(purpose=sender.purpose)])
            if group_by_author:
                body.extend(["", template["grouping"]])
            body.extend(["", template["closing"], sender.name])
            text = "\n".join(body) + "\n"
            bundle.append(f"Subject: {subject}\nTo:\n\n{text}")
            message = EmailMessage(policy=policy.SMTP)
            message["To"] = ""
            message["Subject"] = subject
            message.set_content(text, charset="utf-8")
            messages.append(RenderedFile(f"request_{number}.eml", "message/rfc822", message.as_bytes()))
        return [
            RenderedFile("request_drafts.txt", "text/plain", "\n---\n\n".join(bundle).encode())
        ] + messages
