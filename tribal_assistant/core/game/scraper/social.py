"""Social screens in, plain dicts out: inbox, one conversation, friends, tribe lists, members and forum."""

import re
import unicodedata
from typing import Any
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup, Tag

INTRO_THREAD = re.compile(r"apresent|recrut|boas[\s-]*vindas|novos membros|candidat|bem[\s-]*vindo", re.I)
LEADER = re.compile(r"l[íi]der|fundador|duque|recrutador|diplomata", re.I)


def param(href: str, name: str) -> str | None:
    values = parse_qs(urlparse(href).query).get(name)
    return values[0] if values else None


def number(text: str) -> int:
    digits = re.sub(r"\D", "", text)
    return int(digits) if digits else 0


def text_of(node: Tag | None) -> str:
    return " ".join(node.get_text(" ", strip=True).split()) if node is not None else ""


def player_link(node: Tag) -> Tag | None:
    for link in node.select('a[href*="screen=info_player"]'):
        if param(str(link.get("href", "")), "id"):
            return link
    return None


class InboxParser:
    @staticmethod
    def inbox(html: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html, "lxml")
        items: list[dict[str, Any]] = []
        seen: set[str] = set()
        for row in soup.select("tr"):
            link = row.select_one('a[href*="screen=mail"][href*="mode=view"][href*="view="]')
            if link is None or row.select("tr"):
                continue

            mail_id = param(str(link["href"]), "view")
            if not mail_id or mail_id in seen:
                continue

            seen.add(mail_id)
            cells = row.find_all("td", recursive=False)
            sender_cell = cells[1] if len(cells) > 1 else row
            sender = player_link(sender_cell)
            icons = " ".join(str(img.get("src", "")) for img in row.select("img"))
            items.append(
                {
                    "id": mail_id,
                    "subject": text_of(link),
                    "sender": text_of(sender) if sender else text_of(sender_cell),
                    "sender_id": param(str(sender["href"]), "id") if sender else None,
                    "unread": "new_mail" in " ".join(str(img.get("src", "")) for img in link.select("img")),
                    "answered": "answered_mail" in icons,
                    "date": text_of(cells[2]) if len(cells) > 2 else "",
                    "system": sender is None,
                }
            )

        return items

    @staticmethod
    def thread(html: str) -> dict[str, Any]:
        soup = BeautifulSoup(html, "lxml")
        root = soup.select_one("#content_value") or soup
        messages = []
        for post in root.select("div.post"):
            author = player_link(post.select_one(".igmline") or post) or post.select_one(".author")
            messages.append(
                {
                    "author": text_of(author),
                    "author_id": param(str(author.get("href", "")), "id") if author is not None and author.name == "a" else None,
                    "date": text_of(post.select_one(".date")),
                    "text": text_of(post.select_one(".text")) or text_of(post),
                }
            )

        if not messages:
            sender = player_link(root)
            body = root.select_one(".text, #message, .post")
            messages.append(
                {
                    "author": text_of(sender),
                    "author_id": param(str(sender["href"]), "id") if sender else None,
                    "date": "",
                    "text": text_of(body) or text_of(root)[:2000],
                }
            )

        heading = root.select_one("h2, th")
        return {
            "subject": text_of(heading),
            "messages": messages,
            "can_reply": root.select_one('form[action*="screen=mail"] textarea, a[href*="answer"], a[href*="reply"]') is not None,
        }


class BuddiesParser:
    @staticmethod
    def parse(html: str) -> dict[str, list[dict[str, Any]]]:
        soup = BeautifulSoup(html, "lxml")
        result: dict[str, list[dict[str, Any]]] = {"friends": [], "incoming": [], "outgoing": []}
        seen: set[str] = set()
        for row in soup.select("#content_value tr, body > table tr"):
            player = player_link(row)
            if player is None or row.select("tr"):
                continue

            player_id = param(str(player["href"]), "id") or ""
            if player_id in seen:
                continue

            seen.add(player_id)
            actions = {param(str(a.get("href", "")), "action") or "" for a in row.select("a[href]")}
            accept = next((a for a in row.select('a[href*="accept"]')), None)
            entry = {"name": text_of(player), "player_id": player_id}
            if accept is not None:
                href = str(accept["href"])
                entry["id"] = param(href, "buddy_id") or param(href, "id") or player_id
                result["incoming"].append(entry)
            elif any("cancel" in a or "withdraw" in a for a in actions):
                result["outgoing"].append(entry)
            else:
                result["friends"].append(entry)

        return result

    @staticmethod
    def can_add(html: str) -> bool:
        return BeautifulSoup(html, "lxml").select_one('form[action*="add_buddy"] input[name="name"]') is not None


class TribeParser:
    @staticmethod
    def _table(soup: BeautifulSoup, heads: tuple[str, ...]) -> Tag | None:
        def normalized(value: str) -> str:
            return "".join(c for c in unicodedata.normalize("NFKD", value.casefold()) if not unicodedata.combining(c))
        for table in soup.select("table.vis"):
            th = table.select_one("th")
            if th is not None and any(normalized(head) in normalized(th.get_text(" ", strip=True)) for head in heads):
                return table
        return None

    @classmethod
    def state(cls, html: str) -> dict[str, Any]:
        soup = BeautifulSoup(html, "lxml")
        nearby_table = cls._table(soup, ("Tribos em sua área", "Kmene v okolí", "Kmene vo vašom okolí", "Kmene v tvojej oblasti"))
        invites_table = cls._table(soup, ("Convites", "Pozvánky", "Pozvania"))
        applications_table = cls._table(soup, ("Candidatura", "Žiadosti", "Žiadosti o prijatie", "Prihlášky"))
        create_form = soup.select_one('form[action*="action=create"]') is not None
        # Never infer membership from absent Portuguese-language tables.
        member_link = soup.select_one('a[href*="screen=ally"][href*="mode=members"]') is not None
        recognized = bool(nearby_table or invites_table or applications_table or create_form or member_link)
        in_tribe = bool(member_link and not create_form)

        nearby = []
        for row in nearby_table.select("tr") if nearby_table else []:
            apply = row.select_one('a[href*="mode=apply"]')
            cells = row.find_all("td")
            if apply is None or len(cells) < 3:
                continue
            nearby.append({"id": param(str(apply["href"]), "id"), "tag": text_of(cells[0]), "members": number(cells[1].get_text()), "points": number(cells[2].get_text())})

        invites = []
        for row in invites_table.select("tr") if invites_table else []:
            accept = row.select_one('a[href*="accept"]')
            if accept is None:
                continue
            href = str(accept["href"])
            ally = row.select_one('a[href*="info_ally"]')
            invites.append(
                {
                    "id": param(href, "id") or param(href, "invite_id") or href,
                    "tag": (text_of(ally) or text_of(row))[:40],
                    "ally_id": param(str(ally["href"]), "id") if ally else None,
                }
            )

        applications = []
        for row in applications_table.select("tr") if applications_table else []:
            ally = row.select_one('a[href*="info_ally"]')
            if ally is None:
                continue
            applications.append({"ally_id": param(str(ally["href"]), "id"), "tag": text_of(ally)})

        return {"in_tribe": in_tribe, "screen_recognized": recognized,
                "applications_visible": applications_table is not None,
                "nearby": nearby, "invites": invites, "applications": applications}

    @staticmethod
    def members(html: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html, "lxml")
        members = []
        seen: set[str] = set()
        for row in soup.select("#content_value tr, body > table tr"):
            player = player_link(row)
            if player is None or row.select("tr"):
                continue
            player_id = param(str(player["href"]), "id") or ""
            if player_id in seen:
                continue
            seen.add(player_id)
            cells = row.find_all("td")
            points = max((number(c.get_text()) for c in cells[1:] if re.fullmatch(r"[\d.\s]+", c.get_text().strip() or "x")), default=0)
            members.append({"name": text_of(player), "player_id": player_id, "points": points, "leader": bool(LEADER.search(text_of(row)))})

        return members

    @staticmethod
    def threads(html: str) -> list[dict[str, Any]]:
        soup = BeautifulSoup(html, "lxml")
        threads = []
        seen: set[str] = set()
        for link in soup.select('a[href*="thread_id="]'):
            thread_id = param(str(link["href"]), "thread_id")
            title = text_of(link)
            if not thread_id or not title or thread_id in seen:
                continue
            seen.add(thread_id)
            threads.append({"id": thread_id, "forum_id": param(str(link["href"]), "forum_id"), "title": title, "intro": bool(INTRO_THREAD.search(title))})

        return threads

    @staticmethod
    def forums(html: str) -> list[str]:
        soup = BeautifulSoup(html, "lxml")
        found: list[str] = []
        for link in soup.select('a[href*="forum_id="]'):
            href = str(link["href"])
            forum_id = param(href, "forum_id")
            if forum_id and "thread_id=" not in href and forum_id not in found:
                found.append(forum_id)
        return found

    @staticmethod
    def content(html: str, limit: int = 4000) -> str:
        soup = BeautifulSoup(html, "lxml")
        root = soup.select_one("#content_value") or soup
        for node in root.select("script, style"):
            node.decompose()
        return text_of(root)[:limit]
