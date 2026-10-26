enum OYB_EArsenalCategory
{
	ALL,
	WEAPONS,
	AMMO,
	ATTACHMENTS,
	CLOTHING,
	VESTS_BAGS,
	MEDICAL,
	THROWABLES,
	EQUIPMENT,
	OTHER
}

class OYB_ArsenalCategories
{
	static const int COUNT = 10;

	static string GetName(int category)
	{
		switch (category)
		{
			case OYB_EArsenalCategory.ALL: return "All";
			case OYB_EArsenalCategory.WEAPONS: return "Weapons";
			case OYB_EArsenalCategory.AMMO: return "Ammo";
			case OYB_EArsenalCategory.ATTACHMENTS: return "Attachments";
			case OYB_EArsenalCategory.CLOTHING: return "Clothing";
			case OYB_EArsenalCategory.VESTS_BAGS: return "Vests / Bags";
			case OYB_EArsenalCategory.MEDICAL: return "Medical";
			case OYB_EArsenalCategory.THROWABLES: return "Grenades / Explosives";
			case OYB_EArsenalCategory.EQUIPMENT: return "Tools";
		}
		return "Other";
	}

	// Ammo goes by item mode because magazines share item types with their weapons.
	static int FromItem(SCR_EArsenalItemType type, SCR_EArsenalItemMode mode)
	{
		if (mode == SCR_EArsenalItemMode.AMMUNITION)
			return OYB_EArsenalCategory.AMMO;

		if (type & (SCR_EArsenalItemType.RIFLE | SCR_EArsenalItemType.PISTOL | SCR_EArsenalItemType.MACHINE_GUN | SCR_EArsenalItemType.SNIPER_RIFLE | SCR_EArsenalItemType.ROCKET_LAUNCHER))
			return OYB_EArsenalCategory.WEAPONS;
		if (type & SCR_EArsenalItemType.WEAPON_ATTACHMENT)
			return OYB_EArsenalCategory.ATTACHMENTS;
		if (type & (SCR_EArsenalItemType.HEADWEAR | SCR_EArsenalItemType.TORSO | SCR_EArsenalItemType.LEGS | SCR_EArsenalItemType.FOOTWEAR | SCR_EArsenalItemType.HANDWEAR))
			return OYB_EArsenalCategory.CLOTHING;
		if (type & (SCR_EArsenalItemType.VEST_AND_WAIST | SCR_EArsenalItemType.BACKPACK | SCR_EArsenalItemType.RADIO_BACKPACK))
			return OYB_EArsenalCategory.VESTS_BAGS;
		if (type & SCR_EArsenalItemType.HEAL)
			return OYB_EArsenalCategory.MEDICAL;
		if (type & (SCR_EArsenalItemType.LETHAL_THROWABLE | SCR_EArsenalItemType.NON_LETHAL_THROWABLE | SCR_EArsenalItemType.EXPLOSIVES))
			return OYB_EArsenalCategory.THROWABLES;
		if (type & SCR_EArsenalItemType.EQUIPMENT)
			return OYB_EArsenalCategory.EQUIPMENT;

		return OYB_EArsenalCategory.OTHER;
	}
}

class OYB_ArsenalEntry
{
	ResourceName m_sPrefab;
	string m_sName;
	string m_sSearch;
	bool m_bHasName;
	SCR_EArsenalItemType m_eType;
	SCR_EArsenalItemMode m_eMode;
	int m_iCategory;
	int m_iSupplyCost;
	SCR_ECharacterRank m_eRank;
}

// Reads what one arsenal actually offers, through the arsenal's own filtered list:
// its overwrite config if it has one, otherwise its faction catalogue with its
// item types and modes. Nothing installed but not offered here is ever listed.
// The list is kept until the arsenal says its offer changed.
class OYB_ArsenalCatalogService
{
	protected static ref OYB_ArsenalCatalogService s_Instance;

	protected SCR_ArsenalComponent m_CachedArsenal;
	protected ref array<ref OYB_ArsenalEntry> m_aCachedEntries;

	static OYB_ArsenalCatalogService GetInstance()
	{
		if (!s_Instance)
			s_Instance = new OYB_ArsenalCatalogService();

		return s_Instance;
	}

	array<ref OYB_ArsenalEntry> GetEntries(notnull SCR_ArsenalComponent arsenal, out bool fromCache)
	{
		fromCache = m_CachedArsenal == arsenal && m_aCachedEntries != null;
		if (fromCache)
			return m_aCachedEntries;

		if (m_CachedArsenal && m_CachedArsenal != arsenal)
			m_CachedArsenal.GetOnArsenalUpdated().Remove(OnArsenalUpdated);

		if (m_CachedArsenal != arsenal)
			arsenal.GetOnArsenalUpdated().Insert(OnArsenalUpdated);

		m_CachedArsenal = arsenal;
		m_aCachedEntries = Build(arsenal);
		return m_aCachedEntries;
	}

	protected void OnArsenalUpdated(array<ResourceName> arsenalItems)
	{
		Print("OYB_ARSENAL_UI arsenal offer changed, item list will be rebuilt on next open");
		m_aCachedEntries = null;
	}

	protected array<ref OYB_ArsenalEntry> Build(SCR_ArsenalComponent arsenal)
	{
		array<ref OYB_ArsenalEntry> entries = {};
		array<SCR_ArsenalItem> items = {};
		arsenal.GetFilteredArsenalItems(items);

		SCR_EArsenalSupplyCostType costType = arsenal.GetSupplyCostType();
		SCR_Faction faction = arsenal.GetAssignedFaction();
		SCR_EntityCatalogManagerComponent catalogManager = SCR_EntityCatalogManagerComponent.GetInstance();
		map<ResourceName, bool> seen = new map<ResourceName, bool>();
		int unnamed;

		foreach (SCR_ArsenalItem item : items)
		{
			if (!item)
				continue;

			ResourceName prefab = item.GetItemResourceName();
			if (prefab.IsEmpty() || seen.Contains(prefab))
				continue;
			seen.Insert(prefab, true);

			OYB_ArsenalEntry entry = new OYB_ArsenalEntry();
			entry.m_sPrefab = prefab;
			entry.m_eType = item.GetItemType();
			entry.m_eMode = item.GetItemMode();
			entry.m_iCategory = OYB_ArsenalCategories.FromItem(entry.m_eType, entry.m_eMode);
			entry.m_iSupplyCost = item.GetSupplyCost(costType);
			entry.m_eRank = item.GetRequiredRank();

			// The display name lives on the catalogue entry, not on the arsenal item.
			string name = string.Empty;
			SCR_EntityCatalogEntry catalogEntry = null;
			if (catalogManager)
				catalogEntry = catalogManager.GetEntryWithPrefabFromAnyCatalog(EEntityCatalogType.ITEM, prefab, faction);
			if (catalogEntry)
				name = WidgetManager.Translate(catalogEntry.GetEntityName());

			// The game names an item MISSING when its prefab has no UI info.
			entry.m_bHasName = !name.IsEmpty() && name != "MISSING";
			if (!entry.m_bHasName)
			{
				name = FilePath.StripExtension(FilePath.StripPath(prefab.GetPath()));
				unnamed++;
			}
			entry.m_sName = name;

			string search = name + " " + FilePath.StripPath(prefab.GetPath());
			search.ToLower();
			entry.m_sSearch = search;

			entries.Insert(entry);
		}

		PrintFormat("OYB_ARSENAL_UI built item list: %1 entries from arsenal, %2 unique, %3 without a display name", items.Count(), entries.Count(), unnamed);
		return entries;
	}
}
