"""One-time safe reclassification of older SK106 reports; never invent dates."""
import asyncio
import json
from datetime import datetime, timezone
from pathlib import Path
from sqlalchemy import select
from tribal_assistant.core.db.session import SessionFactory
from tribal_assistant.core.models.player import Player
from tribal_assistant.core.models.account import Account
from tribal_assistant.core.models.report import Report
from tribal_assistant.core.game.locale_text import report_category

OUT=Path("/app/storage/sk106-locale-category-backfill-20261012.json")

async def main():
    async with SessionFactory() as session:
        people=(await session.execute(select(Player).where(
            Player.name=="chlpatucky",Player.world=="sk106"))).scalars().all()
        if len(people)!=1:
            raise RuntimeError("PLAYER_IDENTITY_NOT_UNIQUE")
        player=people[0]
        account=(await session.execute(select(Account).where(Account.id==player.account_id))).scalar_one()
        if account.username!="chlpatucky" or account.server!="sk106":
            raise RuntimeError("ACCOUNT_SERVER_IDENTITY_MISMATCH")
        rows=(await session.execute(select(Report).where(
            Report.account_id==player.account_id))).scalars().all()
        changes=[]
        for row in rows:
            corrected=report_category(row.title)
            if row.category=="other" and corrected!="other":
                changes.append({"id":row.id,"game_id":row.game_id,"before":row.category,
                                "after":corrected,"title":row.title})
                row.category=corrected
        backup={"at":datetime.now(timezone.utc).isoformat(),"account_id":player.account_id,
                "world":player.world,"total_rows":len(rows),"changes":changes}
        if OUT.exists():
            raise RuntimeError("BACKUP_ALREADY_EXISTS_REFUSE_REPEAT")
        OUT.write_text(json.dumps(backup,ensure_ascii=False,indent=2)+"\n")
        await session.commit()
        print(json.dumps({"total_reports":len(rows),"reclassified":len(changes),
                          "categories":{cat:sum(1 for x in changes if x["after"]==cat)
                                        for cat in ("attack","scout","achievement","support","trade")},
                          "backup_path":str(OUT)},ensure_ascii=False))

if __name__=="__main__":
    asyncio.run(main())
