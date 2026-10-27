enum OYB_EArsenalCategory
{
	ALL,
	FITS_WEAPON,
	RIFLES,
	MACHINE_GUNS,
	SNIPER_RIFLES,
	PISTOLS,
	LAUNCHERS,
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
	static const int COUNT = 15;

	static string GetName(int category)
	{
		switch (category)
		{
			case OYB_EArsenalCategory.ALL: return "All";
			case OYB_EArsenalCategory.FITS_WEAPON: return "Fits weapon";
			case OYB_EArsenalCategory.RIFLES: return "Rifles";
			case OYB_EArsenalCategory.MACHINE_GUNS: return "Machine guns";
			case OYB_EArsenalCategory.SNIPER_RIFLES: return "Sniper rifles";
			case OYB_EArsenalCategory.PISTOLS: return "Pistols";
			case OYB_EArsenalCategory.LAUNCHERS: return "Launchers";
			case OYB_EArsenalCategory.AMMO: return "Ammo";
			case OYB_EArsenalCategory.ATTACHMENTS: return "Attachments";
			case OYB_EArsenalCategory.CLOTHING: return "Clothing";
			case OYB_EArsenalCategory.VESTS_BAGS: return "Vests & bags";
			case OYB_EArsenalCategory.MEDICAL: return "Medical";
			case OYB_EArsenalCategory.THROWABLES: return "Explosives";
			case OYB_EArsenalCategory.EQUIPMENT: return "Tools";
		}
		return "Other";
	}

	// Ammo goes by item mode because magazines share item types with their weapons.
	// Rockets, mortar shells and other explosive rounds go with the explosives;
	// single 40mm rounds are sorted out once the arsenal's items are on screen
	// (see OYB_ArsenalCatalogService.SortRounds).
	static int FromItem(SCR_EArsenalItemType type, SCR_EArsenalItemMode mode)
	{
		if (mode == SCR_EArsenalItemMode.AMMUNITION)
		{
			if (type & (SCR_EArsenalItemType.ROCKET_LAUNCHER | SCR_EArsenalItemType.MORTARS | SCR_EArsenalItemType.EXPLOSIVES))
				return OYB_EArsenalCategory.THROWABLES;

			return OYB_EArsenalCategory.AMMO;
		}

		if (type & SCR_EArsenalItemType.RIFLE)
			return OYB_EArsenalCategory.RIFLES;
		if (type & SCR_EArsenalItemType.MACHINE_GUN)
			return OYB_EArsenalCategory.MACHINE_GUNS;
		if (type & SCR_EArsenalItemType.SNIPER_RIFLE)
			return OYB_EArsenalCategory.SNIPER_RIFLES;
		if (type & SCR_EArsenalItemType.PISTOL)
			return OYB_EArsenalCategory.PISTOLS;
		if (type & SCR_EArsenalItemType.ROCKET_LAUNCHER)
			return OYB_EArsenalCategory.LAUNCHERS;
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
	protected ref map<ResourceName, OYB_ArsenalEntry> m_mCachedByPrefab = new map<ResourceName, OYB_ArsenalEntry>();
	protected bool m_bRoundsSorted;

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
		m_bRoundsSorted = false;

		m_mCachedByPrefab.Clear();
		foreach (OYB_ArsenalEntry entry : m_aCachedEntries)
			m_mCachedByPrefab.Insert(entry.m_sPrefab, entry);

		return m_aCachedEntries;
	}

	// Grenade rounds, rockets and shells are ammunition to the arsenal, the same
	// as rifle magazines. They hold a single round, so an ammo item that holds
	// one round (or isn't a magazine at all) moves to Explosives. This needs the
	// item itself, so it's done with the arsenal's preview items, once per list.
	void SortRounds(notnull SCR_ArsenalComponent arsenal, notnull array<IEntity> items, notnull array<ResourceName> prefabs)
	{
		bool fromCache;
		GetEntries(arsenal, fromCache);
		if (m_bRoundsSorted || items.Count() != prefabs.Count())
			return;
		m_bRoundsSorted = true;

		int moved;
		foreach (int i, IEntity item : items)
		{
			OYB_ArsenalEntry entry = FindEntry(arsenal, prefabs[i]);
			if (!item || !entry || entry.m_iCategory != OYB_EArsenalCategory.AMMO)
				continue;

			BaseMagazineComponent magazine = BaseMagazineComponent.Cast(item.FindComponent(BaseMagazineComponent));
			if (magazine && magazine.GetMaxAmmoCount() > 1)
				continue;

			entry.m_iCategory = OYB_EArsenalCategory.THROWABLES;
			moved++;
		}

		if (moved > 0)
			PrintFormat("OYB_ARSENAL moved %1 single-round items from Ammo to Explosives", moved);
	}

	OYB_ArsenalEntry FindEntry(notnull SCR_ArsenalComponent arsenal, ResourceName prefab)
	{
		bool fromCache;
		GetEntries(arsenal, fromCache);
		return m_mCachedByPrefab.Get(prefab);
	}

	protected void OnArsenalUpdated(array<ResourceName> arsenalItems)
	{
		Print("OYB_ARSENAL arsenal offer changed, item list will be rebuilt on next open");
		m_aCachedEntries = null;
		m_mCachedByPrefab.Clear();
	}

	// The name the inventory tooltip shows for a prefab.
	static string OYB_ReadInventoryName(ResourceName prefab)
	{
		Resource resource = Resource.Load(prefab);
		if (!resource || !resource.IsValid())
			return string.Empty;

		IEntityComponentSource itemSource = SCR_ComponentHelper.GetInventoryItemComponentSource(SCR_BaseContainerTools.FindEntitySource(resource));
		if (!itemSource)
			return string.Empty;

		SCR_ItemAttributeCollection attributes = SCR_ComponentHelper.GetInventoryItemInfo(itemSource);
		if (!attributes || !attributes.GetUIInfo())
			return string.Empty;

		return WidgetManager.Translate(attributes.GetUIInfo().GetName());
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

			// The name the inventory tooltip shows comes from the item's inventory
			// attributes. The catalogue entry prefers the weapon's own name, which
			// variants share (an M16A2 carbine is still "M16A2" there), so it's only
			// the fallback.
			string name = OYB_ReadInventoryName(prefab);
			if (name.IsEmpty() && catalogManager)
			{
				SCR_EntityCatalogEntry catalogEntry = catalogManager.GetEntryWithPrefabFromAnyCatalog(EEntityCatalogType.ITEM, prefab, faction);
				if (catalogEntry)
					name = WidgetManager.Translate(catalogEntry.GetEntityName());
			}

			// The game names an item MISSING when its prefab has no UI info.
			entry.m_bHasName = !name.IsEmpty() && name != "MISSING";
			if (!entry.m_bHasName)
			{
				name = FilePath.StripExtension(FilePath.StripPath(prefab.GetPath()));
				unnamed++;
			}
			entry.m_sName = name;

			// Search matches what the player sees, not the prefab's file name.
			string search = name;
			search.ToLower();
			entry.m_sSearch = search;

			entries.Insert(entry);
		}

		PrintFormat("OYB_ARSENAL built item list: %1 entries from arsenal, %2 unique, %3 without a display name", items.Count(), entries.Count(), unnamed);
		return entries;
	}
}
