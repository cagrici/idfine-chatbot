"""Seed initial data into the database."""

import asyncio

from sqlalchemy import select

from app.core.security import hash_password
from app.db.database import async_session, engine, Base
from app.models.source_group import SourceGroup
from app.models.user import User
from app.models.role import Role
from app.models.activity_log import ActivityLog
from app.models.conversation import Conversation, Message
from app.models.document import Document, DocumentChunk
from app.models.audit import OdooSyncLog
from app.models.canned_response import CannedResponse
from app.models.product import Product


ROLE_DEFINITIONS = {
    "admin": {
        "display_name": "Admin",
        "description": "Tam yetkili sistem yoneticisi",
        "level": 100,
        "is_system": True,
        "permissions": {
            "admin.full_access": True,
            "users.view": True,
            "users.create": True,
            "users.edit": True,
            "users.delete": True,
            "users.reset_password": True,
            "users.assign_role": True,
            "documents.view": True,
            "documents.upload": True,
            "documents.delete": True,
            "documents.reindex": True,
            "source_groups.view": True,
            "source_groups.create": True,
            "source_groups.edit": True,
            "source_groups.delete": True,
            "conversations.view": True,
            "conversations.respond": True,
            "conversations.escalate": True,
            "canned_responses.manage": True,
            "settings.view": True,
            "settings.edit": True,
            "stats.view": True,
        },
    },
    "manager": {
        "display_name": "Yonetici",
        "description": "Kullanici ve dokuman yonetimi, sohbet goruntuleme",
        "level": 50,
        "is_system": True,
        "permissions": {
            "users.view": True,
            "users.create": True,
            "users.edit": True,
            "users.delete": True,
            "users.reset_password": True,
            "users.assign_role": True,
            "documents.view": True,
            "documents.upload": True,
            "documents.delete": True,
            "documents.reindex": True,
            "source_groups.view": True,
            "source_groups.create": True,
            "source_groups.edit": True,
            "source_groups.delete": True,
            "conversations.view": True,
            "conversations.respond": True,
            "conversations.escalate": True,
            "canned_responses.manage": True,
            "settings.view": True,
            "stats.view": True,
        },
    },
    "agent": {
        "display_name": "Temsilci",
        "description": "Sohbet yonetimi ve dokuman goruntuleme",
        "level": 25,
        "is_system": True,
        "permissions": {
            "documents.view": True,
            "conversations.view": True,
            "conversations.respond": True,
            "conversations.escalate": True,
            "stats.view": True,
        },
    },
    "viewer": {
        "display_name": "Izleyici",
        "description": "Salt okunur erisim",
        "level": 10,
        "is_system": True,
        "permissions": {
            "conversations.view": True,
            "stats.view": True,
        },
    },
}


async def create_tables():
    """Create all tables."""
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)


async def seed_roles():
    """Create or update default roles."""
    async with async_session() as session:
        for name, definition in ROLE_DEFINITIONS.items():
            result = await session.execute(select(Role).where(Role.name == name))
            existing = result.scalar_one_or_none()
            if existing:
                existing.permissions = definition["permissions"]
                existing.display_name = definition["display_name"]
                existing.description = definition["description"]
                existing.level = definition["level"]
                print(f"  Role updated: {name}")
            else:
                role = Role(
                    name=name,
                    display_name=definition["display_name"],
                    description=definition["description"],
                    level=definition["level"],
                    is_system=definition["is_system"],
                    permissions=definition["permissions"],
                )
                session.add(role)
                print(f"  Role created: {name}")
        await session.commit()


async def seed_admin():
    """Create default admin user if not exists, link to admin role."""
    async with async_session() as session:
        # Get admin role
        role_result = await session.execute(select(Role).where(Role.name == "admin"))
        admin_role = role_result.scalar_one_or_none()

        result = await session.execute(
            select(User).where(User.email == "admin@idfine.com")
        )
        existing = result.scalar_one_or_none()
        if existing:
            updated = False
            if existing.role != "admin":
                existing.role = "admin"
                updated = True
            if admin_role and existing.role_id != admin_role.id:
                existing.role_id = admin_role.id
                updated = True
            if updated:
                await session.commit()
                print("  Admin user updated with role_id")
            else:
                print("  Admin user already exists")
            return

        admin = User(
            email="admin@idfine.com",
            password_hash=hash_password("admin123"),  # Change in production!
            full_name="Admin",
            role="admin",
            role_id=admin_role.id if admin_role else None,
        )
        session.add(admin)
        await session.commit()
        print("  Admin user created: admin@idfine.com")


async def backfill_role_ids():
    """Backfill role_id for existing users that don't have it set."""
    async with async_session() as session:
        roles_result = await session.execute(select(Role))
        role_map = {r.name: r.id for r in roles_result.scalars().all()}

        result = await session.execute(
            select(User).where(User.role_id.is_(None))
        )
        users = result.scalars().all()
        count = 0
        for u in users:
            if u.role in role_map:
                u.role_id = role_map[u.role]
                count += 1
            elif "viewer" in role_map:
                u.role = "viewer"
                u.role_id = role_map["viewer"]
                count += 1
        if count:
            await session.commit()
            print(f"  Backfilled role_id for {count} users")
        else:
            print("  No users need role_id backfill")


SOURCE_GROUP_DEFINITIONS = {
    "public": {
        "name": "Genel (Müşteri)",
        "description": "Müşterilere açık genel bilgiler, ürün katalogları",
        "color": "#10b981",
        "is_default": True,
        "data_permissions": {
            "rag_enabled": True,
            "product_db_enabled": True,
            "odoo_enabled": True,
            "odoo_scopes": ["orders", "deliveries", "tickets"],
        },
    },
    "internal": {
        "name": "Dahili (Çalışan)",
        "description": "Çalışanlara yönelik iç dokümanlar ve tam ERP erişimi",
        "color": "#3b82f6",
        "is_default": False,
        "data_permissions": {
            "rag_enabled": True,
            "product_db_enabled": True,
            "odoo_enabled": True,
            "odoo_scopes": ["orders", "invoices", "deliveries", "tickets", "partners"],
        },
    },
    "management": {
        "name": "Yönetim",
        "description": "Üst yönetim için tam erişim (finansal veriler dahil)",
        "color": "#8b5cf6",
        "is_default": False,
        "data_permissions": {
            "rag_enabled": True,
            "product_db_enabled": True,
            "odoo_enabled": True,
            "odoo_scopes": [
                "orders", "invoices", "deliveries", "tickets",
                "partners", "financials", "reports",
            ],
        },
    },
}


async def seed_source_groups():
    """Create or update default source groups."""
    async with async_session() as session:
        for slug, definition in SOURCE_GROUP_DEFINITIONS.items():
            result = await session.execute(
                select(SourceGroup).where(SourceGroup.slug == slug)
            )
            existing = result.scalar_one_or_none()
            if existing:
                existing.name = definition["name"]
                existing.description = definition["description"]
                existing.color = definition["color"]
                existing.data_permissions = definition["data_permissions"]
                existing.is_default = definition["is_default"]
                print(f"  Source group updated: {slug}")
            else:
                sg = SourceGroup(
                    name=definition["name"],
                    slug=slug,
                    description=definition["description"],
                    color=definition["color"],
                    data_permissions=definition["data_permissions"],
                    is_default=definition["is_default"],
                )
                session.add(sg)
                print(f"  Source group created: {slug}")
        await session.commit()


async def backfill_source_groups():
    """Assign existing documents and widget configs to the default source group."""
    async with async_session() as session:
        # Find default source group
        result = await session.execute(
            select(SourceGroup).where(SourceGroup.is_default == True)
        )
        default_sg = result.scalar_one_or_none()
        if not default_sg:
            print("  No default source group found, skipping backfill")
            return

        # Backfill documents
        from sqlalchemy import update
        doc_result = await session.execute(
            update(Document)
            .where(Document.source_group_id.is_(None))
            .values(source_group_id=default_sg.id)
        )
        doc_count = doc_result.rowcount

        # Backfill widget configs
        from app.models.widget_config import WidgetConfig
        wc_result = await session.execute(
            update(WidgetConfig)
            .where(WidgetConfig.source_group_id.is_(None))
            .values(source_group_id=default_sg.id)
        )
        wc_count = wc_result.rowcount

        await session.commit()
        if doc_count or wc_count:
            print(f"  Backfilled source_group_id: {doc_count} documents, {wc_count} widget configs")
        else:
            print("  No documents or widget configs need source_group backfill")


CANNED_RESPONSE_SEEDS = [
    {
        "title": "Şikayet: İlk Yanıt",
        "content": "Geri bildiriminiz bizim için çok değerli. 🙏 Yaşadığınız sorunu anlıyorum ve çözmek için elimden geleni yapacağım.\n\nSorununuzu detaylı inceleyebilmem için birkaç bilgiye ihtiyacım var:\n- Sipariş numaranız\n- Sorunun detaylı açıklaması\n- Varsa fotoğraf",
        "category": "sikayet",
        "shortcut": "/sikayet",
    },
    {
        "title": "Garanti Bilgisi",
        "content": "🛡 ID Fine Garanti Kapsamı:\n\n✨ ÖMÜR BOYU KENAR ÇATLAMA GARANTİSİ- Tüm beyaz ve renkli ürünlerde geçerlidir.\n\nGaranti kapsamı dışında kalan durumlar:\n- Mekanik darbeler\n- Yanlış kullanım\n- Aşırı sıcaklık değişimleri",
        "category": "bilgi",
        "shortcut": "/garanti",
    },
    {
        "title": "Bakım Önerileri",
        "content": "🍽 Ürün Bakım Önerileri:\n\n✅ Bulaşık makinesinde yıkayın (maks 65°C)\n✅ Yumuşak deterjan kullanın\n❌ Metal ovma telleri kullanmayın\n❌ Aşırı sıcak-soğuk geçişlerinden kaçının\n\nDoğru bakım ile ürünleriniz yıllarca ilk günkü gibi kalır!",
        "category": "bilgi",
        "shortcut": "/bakim",
    },
    {
        "title": "Özel Tasarım",
        "content": "✏ Özel Tasarım Seçeneklerimiz:\n\n- Kurumsal logo uygulaması\n- Özel renk ve desen tasarımı\n- Şefe özel tabak koleksiyonları\n- Restoran konseptine uygun seri tasarımlar\n\nMinimum sipariş miktarı ve fiyatlandırma için detaylı bilgi almak ister misiniz?",
        "category": "satis",
        "shortcut": "/ozel",
    },
    {
        "title": "Logo Baskı",
        "content": "🏷 Kurumsal Kişiselleştirme Hizmetimiz:\n\n✅ Logo/Amblem uygulaması\n✅ Dijital baskı teknolojisi\n✅ Ömür boyu dayanıklılık\n✅ Bulaşık makinesi güvenli\n\nLogo dosyanızı (vektörel formatta) göndermeniz yeterli!",
        "category": "satis",
        "shortcut": "/logo",
    },
    {
        "title": "Teslimat Süresi",
        "content": "🚚 Teslimat Süreleri:\n\n- Stokta olan ürünler: 3-5 iş günü\n- Özel üretimler: 15-30 iş günü\n- Kişiselleştirilmiş ürünler: 20-45 iş günü\n\nSiparişinizin durumunu takip etmek için sipariş numaranızı paylaşabilir misiniz?",
        "category": "bilgi",
        "shortcut": "/teslimat",
    },
    {
        "title": "Minimum Sipariş",
        "content": "📦 Sipariş Bilgileri:\n\n- Minimum sipariş tutarı koleksiyona göre değişmektedir\n- HoReCa müşterilerimize özel fiyatlandırma sunuyoruz\n- Toplu siparişlerde ek indirimler mevcuttur\n\nSize özel bir teklif hazırlamamızı ister misiniz?",
        "category": "satis",
        "shortcut": "/siparis",
    },
    {
        "title": "Fiyat Listesi Talebi",
        "content": "💰 Fiyat listemiz için bilgilerinize ihtiyacımız var:\n\nLütfen belirtiniz:\n- Firma Adı\n- Vergi No\n- İlgilendiğiniz koleksiyon/ürün grubu\n- Tahmini sipariş miktarı\n\nBu bilgiler doğrultusunda size özel bir fiyat teklifi hazırlayacağız.",
        "category": "satis",
        "shortcut": "/fiyat",
    },
    {
        "title": "Mesai Saatleri İçi",
        "content": "Merhaba! ID Fine müşteri hizmetlerine hoş geldiniz! 👋\n\nBen {{temsilci_adi}}, size yardımcı olmak için buradayım. Hangi konuda destek almak istersiniz?",
        "category": "genel",
        "shortcut": "/merhaba",
    },
    {
        "title": "Katalog Talebi",
        "content": "📘 Dijital kataloğumuza web sitemizden ulaşabilirsiniz: www.idfine.com.tr\n\nBasılı katalog için bilgilerinizi (firma adı, adres, telefon) paylaşabilir misiniz? En kısa sürede gönderelim.",
        "category": "bilgi",
        "shortcut": "/katalog",
    },
    {
        "title": "Ürün Özellikleri",
        "content": "📍 ID Fine Porselen Özellikleri:\n\n✅ Ömür Boyu Kenar Çatlama Garantisi\n✅ Mikrodalga ve Fırın Güvenli\n✅ Bulaşık Makinesi Güvenli (65°C)\n✅ Çizilmeye Dayanıklı Yüzey\n✅ Istifleme Kolaylığı\n✅ Profesyonel HoReCa Kalitesi",
        "category": "bilgi",
        "shortcut": "/ozellik",
    },
    {
        "title": "Koleksiyon Listesi",
        "content": "🏺 ID Fine Koleksiyonlarımız:\n\n🔸 MODERN SERİLER\n• Reckless (Antrasit)\n• Adel (Somon)\n• Mellow (Soft Tonlar)\n\n🔹 KLASİK SERİLER\n• Elegant\n• Royal\n• Heritage\n\nHangi koleksiyonla ilgileniyorsunuz?",
        "category": "bilgi",
        "shortcut": "/koleksiyon",
    },
    {
        "title": "İngilizce Karşılama",
        "content": "Welcome to ID Fine Porcelain! 🌟\n\nDefining dining experiences since 1972. How may we assist you today?\n\nWe offer:\n- Premium porcelain collections\n- Custom branding solutions\n- HoReCa professional products",
        "category": "genel",
        "shortcut": "/hello",
    },
    {
        "title": "Mesai Saatleri Dışı",
        "content": "ID Fine'ı tercih ettiğiniz için teşekkür ederiz! 🌙\n\nŞu anda mesai saatlerimiz dışındayız.\n\nMesai Saatlerimiz:\n📅 Pazartesi - Cuma: 08:30 - 17:30\n\nMesajınızı bırakın, en kısa sürede size dönüş yapacağız!",
        "category": "genel",
        "shortcut": "/mesai",
    },
    {
        "title": "Yeni Koleksiyon",
        "content": "🎉 YENİ: {{urun_adi}} Serisi!\n\nÖne çıkan özellikler:\n- Modern tasarım\n- Profesyonel kullanım uygunluğu\n- Geniş ürün yelpazesi\n\nDetaylı bilgi ve numune talebi için bize ulaşın!",
        "category": "satis",
        "shortcut": "/yeni",
    },
    {
        "title": "Fuar/Etkinlik Duyurusu",
        "content": "🎪 HABER: Fuar Katılımımız!\n\n🏛 Tarih: Yakında duyurulacak\n📍 Yer: Fuar Merkezi\n\nStandımızı ziyaret ederek yeni koleksiyonlarımızı yakından inceleyebilirsiniz!",
        "category": "bilgi",
    },
    {
        "title": "Takip Önerisi",
        "content": "📧 Görüşmemizin özeti e-posta adresinize gönderildi. 🙏\n\nYeni koleksiyonlardan haberdar olmak ister misiniz? E-bültenimize kayıt olabilirsiniz!\n\nBaşka bir sorunuz var mı?",
        "category": "takip",
        "shortcut": "/takip",
    },
    {
        "title": "Standart Kapanış",
        "content": "ID Fine'ı tercih ettiğiniz için teşekkürler! Size yardımcı olabildiysen ne mutlu bana.\n\nBaşka sorulan olursa her zaman buradayım. İyi günler dilerim! 😊",
        "category": "kapanis",
        "shortcut": "/kapan",
    },
    {
        "title": "Teşekkür Mesajı",
        "content": "ID Fine'ı tercih ettiğiniz için teşekkür ederiz! 🙏\n\n📦 Siparişiniz hazırlanıyor.\n🔵 Kargo takip bilgisi SMS ve e-posta ile gönderilecektir.\n\nHerhangi bir sorunuz olursa bize ulaşmaktan çekinmeyin!",
        "category": "kapanis",
        "shortcut": "/tesekkur",
    },
    {
        "title": "Teklif Takibi",
        "content": "Merhaba {{musteri_adi}},\n\nGeçtiğimiz günlerde gönderdiğimiz teklif hakkında görüşlerinizi merak ediyorum. ✨\n\nSize özel hazırlanan teklifimizle ilgili sorularınız varsa yanıtlamaktan memnuniyet duyarım.",
        "category": "takip",
        "shortcut": "/teklif",
    },
    {
        "title": "İletişim Bilgileri",
        "content": "📍 ID Fine İletişim:\n\n🏭 FABRİKALARIMIZ:\n- Kütahya: 1. OSB 12. Cad. No: 2/1\n- Merkez: İstanbul: Akpınar Mah.\n\n📞 Telefon: 0274 XXX XX XX\n📧 E-posta: info@idfine.com.tr\n🌐 Web: www.idfine.com.tr",
        "category": "bilgi",
        "shortcut": "/iletisim",
    },
]


async def seed_canned_responses():
    """Create default canned response templates (owned by admin)."""
    async with async_session() as session:
        # Get admin user as owner
        admin_result = await session.execute(
            select(User).where(User.email == "admin@idfine.com")
        )
        admin = admin_result.scalar_one_or_none()
        if not admin:
            print("  Admin user not found, skipping canned responses")
            return

        # Check if already seeded
        count_result = await session.execute(
            select(CannedResponse.id).limit(1)
        )
        if count_result.scalar_one_or_none():
            print("  Canned responses already exist, skipping")
            return

        for seed in CANNED_RESPONSE_SEEDS:
            cr = CannedResponse(
                title=seed["title"],
                content=seed["content"],
                category=seed["category"],
                scope="global",
                shortcut=seed.get("shortcut"),
                owner_id=admin.id,
            )
            session.add(cr)

        await session.commit()
        print(f"  Seeded {len(CANNED_RESPONSE_SEEDS)} canned responses")


async def main():
    print("Creating tables...")
    await create_tables()
    print("Tables created")

    print("Seeding roles...")
    await seed_roles()
    print("Roles seeded")

    print("Seeding source groups...")
    await seed_source_groups()
    print("Source groups seeded")

    print("Seeding admin user...")
    await seed_admin()

    print("Backfilling role IDs...")
    await backfill_role_ids()

    print("Backfilling source groups...")
    await backfill_source_groups()

    print("Seeding canned responses...")
    await seed_canned_responses()
    print("Canned responses seeded")

    print("Seed complete")


if __name__ == "__main__":
    asyncio.run(main())
