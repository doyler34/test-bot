// Server settings made in the OYB panel's Arsenal page: items to hide, price
// overrides, and items to add that no faction catalogue lists (found by scanning
// the loaded mods). The server reads them from its profile folder, re-reads the
// file every minute so a change applies without a restart, and only sends them
// to players when something actually changed.
//
// $profile:OYB_Arsenal/settings.json (written by the panel):
//   { "version": 1, "hidden": [prefab, ...],
//     "priceItems": [prefab, ...], "priceCosts": [cost, ...],
//     "extraItems": [prefab, ...], "extraCosts": [cost, ...],
//     "extraFactions": ["" for all, or "US,FIA", ...],
//     "extraCategories": [category name, ...],
//     "categoryItems": [prefab, ...], "categoryNames": [category name, ...] }
//
// The last pair moves catalogued items to another category in the arsenal.
//
// The server also writes $profile:OYB_Arsenal/items.json once it has started:
// every item the faction catalogues offer, plus every item prefab in a loaded mod
// that no catalogue lists ("found" 1), so the panel can list them.
//
// The game classes come first in this file so their additions exist by the time
// OYB_ArsenalSettings uses them.

// Hidden items drop out of every arsenal's list and added items join it, on the
// server and on each player's screen alike.
modded class SCR_ArsenalComponent
{
	protected static ref array<SCR_ArsenalComponent> s_aOYB_All = {};

	override protected void OnPostInit(IEntity owner)
	{
		super.OnPostInit(owner);

		if (SCR_Global.IsEditMode())
			return;

		s_aOYB_All.Insert(this);
		GetGame().GetCallqueue().CallLater(OYB_StartSettings, 1000);
	}

	protected void OYB_StartSettings()
	{
		OYB_ArsenalSettings.Get().StartServer();
	}

	SCR_EArsenalItemType OYB_GetSupportedTypes()
	{
		return m_eSupportedArsenalItemTypes;
	}

	override bool GetFilteredArsenalItems(out notnull array<SCR_ArsenalItem> filteredArsenalItems, EArsenalItemDisplayType requiresDisplayType = -1)
	{
		bool found = super.GetFilteredArsenalItems(filteredArsenalItems, requiresDisplayType);

		OYB_ArsenalSettings settings = OYB_ArsenalSettings.Get();
		for (int i = filteredArsenalItems.Count() - 1; i >= 0; i--)
		{
			SCR_ArsenalItem item = filteredArsenalItems[i];
			if (item && settings.IsHidden(item.GetItemResourceName()))
				filteredArsenalItems.Remove(i);
		}

		return found;
	}

	// Added items go after the catalogue's, in the order OYB's item list also
	// appends them, so the arsenal screen's cards and OYB's list stay aligned.
	override bool GetAvailablePrefabs(out notnull array<ResourceName> availablePrefabs)
	{
		bool found = super.GetAvailablePrefabs(availablePrefabs);

		array<ResourceName> extras = {};
		OYB_ArsenalSettings.Get().GetExtrasFor(this, extras);
		availablePrefabs.InsertAll(extras);

		return found || !extras.IsEmpty();
	}

	// Has every arsenal tell its listeners (the arsenal screen's items, OYB's item
	// list) that what it offers has changed.
	static void OYB_RefreshAll()
	{
		for (int i = s_aOYB_All.Count() - 1; i >= 0; i--)
		{
			SCR_ArsenalComponent arsenal = s_aOYB_All[i];
			if (!arsenal)
			{
				s_aOYB_All.Remove(i);
				continue;
			}

			if (arsenal.m_OnArsenalUpdated)
			{
				array<ResourceName> prefabs = {};
				arsenal.GetAvailablePrefabs(prefabs);
				arsenal.m_OnArsenalUpdated.Invoke(prefabs);
			}
		}
	}
}

// Also started from the arsenal manager, so items.json is written as soon as
// the game mode is up, before any arsenal has been placed.
modded class SCR_ArsenalManagerComponent
{
	override void OnPostInit(IEntity owner)
	{
		super.OnPostInit(owner);

		if (!SCR_Global.IsEditMode())
			GetGame().GetCallqueue().CallLater(OYB_StartSettings, 3000);
	}

	protected void OYB_StartSettings()
	{
		OYB_ArsenalSettings.Get().StartServer();
	}
}

// The server's side: hidden items can't be taken however the request was made,
// overridden prices are what's charged and refunded, and added items (which the
// game's own handler would refuse, having no catalogue entry) are handed out by
// OYB with the same checks the game makes.
modded class SCR_ResourcePlayerControllerInventoryComponent
{
	protected float m_fOYB_Charged;

	[RplRpc(RplChannel.Reliable, RplRcver.Server)]
	override protected void RpcAsk_ArsenalRequestItem_(RplId resourceComponentRplId, RplId storageComponentRplId, ResourceName resourceNameItem, EResourceType resourceType)
	{
		OYB_ArsenalSettings settings = OYB_ArsenalSettings.Get();
		if (settings.IsHidden(resourceNameItem))
		{
			PrintFormat("OYB_ARSENAL refused a hidden item: %1", FilePath.StripPath(resourceNameItem.GetPath()), level: LogLevel.WARNING);
			return;
		}

		SCR_ResourceComponent resourceComponent = SCR_ResourceComponent.Cast(Replication.FindItem(resourceComponentRplId));
		if (settings.IsExtra(resourceNameItem))
		{
			OYB_GiveExtra(resourceComponent, storageComponentRplId, resourceNameItem, resourceType);
			return;
		}

		int price;
		if (!settings.GetPrice(resourceNameItem, price) || resourceType != EResourceType.SUPPLIES || !resourceComponent)
		{
			super.RpcAsk_ArsenalRequestItem_(resourceComponentRplId, storageComponentRplId, resourceNameItem, resourceType);
			return;
		}

		SCR_ArsenalComponent arsenal = SCR_ArsenalComponent.FindArsenalComponent(resourceComponent.GetOwner());
		SCR_ResourceConsumer consumer = resourceComponent.GetConsumer(EResourceGeneratorID.DEFAULT, resourceType);
		bool usesSupplies = arsenal && arsenal.IsArsenalUsingSupplies() && consumer;

		float total;
		if (usesSupplies)
		{
			total = price * consumer.GetBuyMultiplier();
			if (consumer.GetAggregatedResourceValue() < total)
				return;
		}

		// The game reports what it charged when the item is handed over.
		m_fOYB_Charged = -1;
		GetOnArsenalItemRequested().Insert(OYB_OnCharged);
		super.RpcAsk_ArsenalRequestItem_(resourceComponentRplId, storageComponentRplId, resourceNameItem, resourceType);
		GetOnArsenalItemRequested().Remove(OYB_OnCharged);

		if (m_fOYB_Charged < 0 || !usesSupplies)
			return;

		OYB_Settle(resourceComponent, consumer, total - m_fOYB_Charged);
	}

	protected void OYB_OnCharged(SCR_ResourceComponent resourceComponent, ResourceName resourceName, IEntity requesterEntity, BaseInventoryStorageComponent inventoryStorageComponent, EResourceType resourceType, int resourceValue)
	{
		m_fOYB_Charged = resourceValue;
	}

	// An added item, with the checks the game's own handler makes: the buyer is
	// alive, the storage is theirs or within reach, the arsenal is within reach
	// and offers the item, and the supplies are there before anything spawns.
	protected void OYB_GiveExtra(SCR_ResourceComponent resourceComponent, RplId storageComponentRplId, ResourceName prefab, EResourceType resourceType)
	{
		SCR_PlayerController controller = SCR_PlayerController.Cast(GetOwner());
		SCR_ChimeraCharacter buyer;
		if (controller)
			buyer = SCR_ChimeraCharacter.Cast(controller.GetControlledEntity());
		if (!buyer || !resourceComponent || !resourceComponent.GetOwner() || resourceType != EResourceType.SUPPLIES)
			return;

		SCR_InventoryStorageManagerComponent inventory = SCR_InventoryStorageManagerComponent.Cast(buyer.GetCharacterController().GetInventoryStorageManager());
		BaseInventoryStorageComponent storage = BaseInventoryStorageComponent.Cast(Replication.FindItem(storageComponentRplId));
		if (!inventory || !storage)
			return;

		IEntity storageParent = storage.GetOwner();
		vector storagePosition = storageParent.GetOrigin();
		while (storageParent)
		{
			if (ChimeraCharacter.Cast(storageParent))
			{
				if (storageParent != buyer)
					return;
				break;
			}
			storageParent = storageParent.GetParent();
		}

		IEntity arsenalEntity = resourceComponent.GetOwner();
		if (!storageParent && vector.DistanceSq(buyer.GetOrigin(), storagePosition) > MAX_REFUNDING_DISTANCE_SQ)
			return;
		if (vector.DistanceSq(arsenalEntity.GetOrigin(), storagePosition) > MAX_REFUNDING_DISTANCE_SQ || !inventory.ValidateStorageRequest(arsenalEntity))
			return;

		SCR_ArsenalComponent arsenal = SCR_ArsenalComponent.FindArsenalComponent(arsenalEntity);
		if (!arsenal)
			return;

		array<ResourceName> offered = {};
		OYB_ArsenalSettings.Get().GetExtrasFor(arsenal, offered);
		if (!offered.Contains(prefab))
		{
			PrintFormat("OYB_ARSENAL refused an added item this arsenal doesn't offer: %1", FilePath.StripPath(prefab.GetPath()), level: LogLevel.WARNING);
			return;
		}

		int price;
		OYB_ArsenalSettings.Get().GetPrice(prefab, price);
		SCR_ResourceConsumer consumer = resourceComponent.GetConsumer(EResourceGeneratorID.DEFAULT, resourceType);
		if (arsenal.IsArsenalUsingSupplies() && price > 0)
		{
			if (!consumer)
				return;

			float cost = price * consumer.GetBuyMultiplier();
			if (consumer.RequestConsumtion(cost).GetReason() != EResourceReason.SUFFICIENT)
				return;
		}

		inventory.TrySpawnPrefabToStorage(prefab, storage, cb: new SCR_PrefabSpawnCallback(storage));
	}

	[RplRpc(RplChannel.Reliable, RplRcver.Server)]
	override protected void RpcAsk_ArsenalRefundItem_(RplId resourceComponentRplId, RplId inventoryItemRplId, EResourceType resourceType)
	{
		InventoryItemComponent itemComponent = InventoryItemComponent.Cast(Replication.FindItem(inventoryItemRplId));
		SCR_ResourceComponent resourceComponent = SCR_ResourceComponent.Cast(Replication.FindItem(resourceComponentRplId));
		IEntity item;
		if (itemComponent)
			item = itemComponent.GetOwner();

		int price;
		if (!item || !item.GetPrefabData() || !resourceComponent || resourceType != EResourceType.SUPPLIES || !OYB_ArsenalSettings.Get().GetPrice(item.GetPrefabData().GetPrefabName(), price))
		{
			super.RpcAsk_ArsenalRefundItem_(resourceComponentRplId, inventoryItemRplId, resourceType);
			return;
		}

		ResourceName prefab = item.GetPrefabData().GetPrefabName();
		SCR_ArsenalComponent arsenal = SCR_ArsenalComponent.FindArsenalComponent(resourceComponent.GetOwner());
		float gameRefund = SCR_ArsenalManagerComponent.GetItemRefundAmount(item, arsenal, false);
		float catalogPrice = OYB_ArsenalSettings.CatalogPrice(prefab, arsenal);
		bool extra = OYB_ArsenalSettings.Get().IsExtra(prefab);

		super.RpcAsk_ArsenalRefundItem_(resourceComponentRplId, inventoryItemRplId, resourceType);

		if (item && !item.IsDeleted())
			return;
		if (!arsenal || !arsenal.IsArsenalUsingSupplies())
			return;

		// An added item has no catalogue price, so the game gave nothing back; it
		// gets the arsenal's usual refund share of its price. Otherwise the
		// game's refund is a share of the catalogue price, and the same share of
		// the new price is what's due.
		float delta;
		if (extra || catalogPrice <= 0)
		{
			SCR_ResourceGenerator generator = resourceComponent.GetGenerator(EResourceGeneratorID.DEFAULT, resourceType);
			float share = 1;
			if (generator)
				share = generator.GetResourceMultiplier();
			delta = price * share - Math.Max(gameRefund, 0);
		}
		else if (gameRefund > 0)
		{
			delta = (price - catalogPrice) * Math.Min(gameRefund / catalogPrice, 1);
		}

		OYB_Settle(resourceComponent, resourceComponent.GetConsumer(EResourceGeneratorID.DEFAULT, resourceType), -delta);
	}

	// A positive amount is taken from the arsenal, a negative one given to it.
	protected void OYB_Settle(SCR_ResourceComponent resourceComponent, SCR_ResourceConsumer consumer, float amount)
	{
		if (amount > 0 && consumer)
		{
			consumer.RequestConsumtion(amount);
		}
		else if (amount < 0)
		{
			SCR_ResourceGenerator generator = resourceComponent.GetGenerator(EResourceGeneratorID.DEFAULT, EResourceType.SUPPLIES);
			if (generator)
				generator.RequestGeneration(-amount);
		}
	}
}

// The price on an arsenal item card follows the override or the added item's
// price.
modded class SCR_ArsenalInventorySlotUI
{
	override float GetTotalResources()
	{
		float total = super.GetTotalResources();
		if (total < 0 || !m_pItem || !m_pItem.GetOwner() || !m_pItem.GetOwner().GetPrefabData())
			return total;

		int price;
		if (!OYB_ArsenalSettings.Get().GetPrice(m_pItem.GetOwner().GetPrefabData().GetPrefabName(), price))
			return total;

		float multiplier = 1;
		if (m_ArsenalResourceComponent)
		{
			SCR_ResourceConsumer consumer = m_ArsenalResourceComponent.GetConsumer(EResourceGeneratorID.DEFAULT, EResourceType.SUPPLIES);
			if (consumer)
				multiplier = consumer.GetBuyMultiplier();
		}

		m_fSupplyCost = price * multiplier;
		return m_fSupplyCost;
	}
}

class OYB_ArsenalExtra
{
	ResourceName m_sPrefab;
	int m_iCost;
	string m_sFactions;
	int m_iCategory;
}

class OYB_ArsenalSettings
{
	protected static const string SETTINGS_FILE = "$profile:OYB_Arsenal/settings.json";
	protected static const string ITEMS_FILE = "$profile:OYB_Arsenal/items.json";
	protected static const string FOLDER = "$profile:OYB_Arsenal";
	protected static const int RELOAD_EVERY_MS = 60000;
	protected static const int SCAN_BATCH = 20;
	protected static const int SCAN_BATCH_GAP_MS = 20;

	protected static ref OYB_ArsenalSettings s_Instance;

	protected ref set<ResourceName> m_Hidden = new set<ResourceName>();
	protected ref map<ResourceName, int> m_Prices = new map<ResourceName, int>();
	protected ref map<ResourceName, ref OYB_ArsenalExtra> m_Extras = new map<ResourceName, ref OYB_ArsenalExtra>();
	protected ref array<ResourceName> m_aExtraOrder = {};
	protected ref map<ResourceName, int> m_Categories = new map<ResourceName, int>();
	protected string m_sLoadedText;
	protected bool m_bServerStarted;

	// The item list being put together for items.json.
	protected ref map<ResourceName, int> m_mListed;
	protected ref array<string> m_aPrefabs;
	protected ref array<string> m_aNames;
	protected ref array<string> m_aCategories;
	protected ref array<string> m_aFactions;
	protected ref array<int> m_aPrices;
	protected ref array<int> m_aFound;
	protected ref array<ResourceName> m_aScanQueue;
	protected int m_iScanUnusable;
	protected int m_iScanStarted;

	static OYB_ArsenalSettings Get()
	{
		if (!s_Instance)
			s_Instance = new OYB_ArsenalSettings();

		return s_Instance;
	}

	bool IsHidden(ResourceName prefab)
	{
		return m_Hidden.Contains(prefab);
	}

	bool IsExtra(ResourceName prefab)
	{
		return m_Extras.Contains(prefab) && !m_Hidden.Contains(prefab);
	}

	// A price set in the panel, or an added item's price.
	bool GetPrice(ResourceName prefab, out int price)
	{
		if (m_Prices.Find(prefab, price))
			return true;

		OYB_ArsenalExtra extra = m_Extras.Get(prefab);
		if (!extra)
			return false;

		price = extra.m_iCost;
		return true;
	}

	// The added items this arsenal offers: its faction, and the kinds of item it
	// was set up to carry.
	void GetExtrasFor(SCR_ArsenalComponent arsenal, notnull array<ResourceName> extras)
	{
		if (m_aExtraOrder.IsEmpty() || !arsenal)
			return;

		string factionKey;
		if (arsenal.GetAssignedFaction())
			factionKey = arsenal.GetAssignedFaction().GetFactionKey();
		SCR_EArsenalItemType supported = arsenal.OYB_GetSupportedTypes();

		foreach (ResourceName prefab : m_aExtraOrder)
		{
			OYB_ArsenalExtra extra = m_Extras.Get(prefab);
			if (!extra || m_Hidden.Contains(prefab))
				continue;
			if (!(OYB_ArsenalCategories.ToArsenalTypes(extra.m_iCategory) & supported))
				continue;
			if (!extra.m_sFactions.IsEmpty())
			{
				array<string> keys = {};
				extra.m_sFactions.Split(",", keys, true);
				if (!keys.Contains(factionKey))
					continue;
			}
			extras.Insert(prefab);
		}
	}

	// A category set in the panel for a catalogued item.
	bool GetCategory(ResourceName prefab, out int category)
	{
		return m_Categories.Find(prefab, category);
	}

	OYB_ArsenalExtra GetExtra(ResourceName prefab)
	{
		return m_Extras.Get(prefab);
	}

	// Everything a player needs, as the lists one network message carries (eight
	// at most, so an added item's factions and category travel together as
	// "factions|category").
	void GetLists(notnull array<string> hidden, notnull array<string> priceItems, notnull array<int> priceCosts, notnull array<string> extraItems, notnull array<int> extraCosts, notnull array<string> extraMeta, notnull array<string> categoryItems, notnull array<string> categoryNames)
	{
		foreach (ResourceName hiddenPrefab : m_Hidden)
			hidden.Insert(hiddenPrefab);

		foreach (ResourceName pricedPrefab, int cost : m_Prices)
		{
			priceItems.Insert(pricedPrefab);
			priceCosts.Insert(cost);
		}

		foreach (ResourceName extraPrefab : m_aExtraOrder)
		{
			OYB_ArsenalExtra extra = m_Extras.Get(extraPrefab);
			extraItems.Insert(extraPrefab);
			extraCosts.Insert(extra.m_iCost);
			extraMeta.Insert(extra.m_sFactions + "|" + OYB_ArsenalCategories.GetName(extra.m_iCategory));
		}

		foreach (ResourceName movedPrefab, int category : m_Categories)
		{
			categoryItems.Insert(movedPrefab);
			categoryNames.Insert(OYB_ArsenalCategories.GetName(category));
		}
	}

	// Takes new settings and has every arsenal rebuild its list, so hidden and
	// added items show (or go) on open arsenal screens too.
	void Apply(notnull array<string> hidden, notnull array<string> priceItems, notnull array<int> priceCosts, notnull array<string> extraItems, notnull array<int> extraCosts, notnull array<string> extraMeta, notnull array<string> categoryItems, notnull array<string> categoryNames)
	{
		m_Hidden.Clear();
		foreach (string prefab : hidden)
			m_Hidden.Insert(prefab);

		m_Prices.Clear();
		int count = Math.Min(priceItems.Count(), priceCosts.Count());
		for (int i = 0; i < count; i++)
		{
			if (priceCosts[i] >= 0)
				m_Prices.Set(priceItems[i], priceCosts[i]);
		}

		m_Extras.Clear();
		m_aExtraOrder.Clear();
		int extraCount = Math.Min(Math.Min(extraItems.Count(), extraCosts.Count()), extraMeta.Count());
		for (int e = 0; e < extraCount; e++)
		{
			if (extraItems[e].IsEmpty() || extraCosts[e] < 0 || m_Extras.Contains(extraItems[e]))
				continue;

			OYB_ArsenalExtra extra = new OYB_ArsenalExtra();
			extra.m_sPrefab = extraItems[e];
			extra.m_iCost = extraCosts[e];
			string factions = extraMeta[e];
			string categoryName;
			int bar = factions.IndexOf("|");
			if (bar != -1)
			{
				categoryName = factions.Substring(bar + 1, factions.Length() - bar - 1);
				factions = factions.Substring(0, bar);
			}
			extra.m_sFactions = factions;
			extra.m_iCategory = OYB_ArsenalCategories.FromName(categoryName);
			m_Extras.Insert(extra.m_sPrefab, extra);
			m_aExtraOrder.Insert(extra.m_sPrefab);
		}

		m_Categories.Clear();
		int movedCount = Math.Min(categoryItems.Count(), categoryNames.Count());
		for (int c = 0; c < movedCount; c++)
		{
			if (!categoryItems[c].IsEmpty())
				m_Categories.Set(categoryItems[c], OYB_ArsenalCategories.FromName(categoryNames[c]));
		}

		OYB_ArsenalCatalogService.GetInstance().Invalidate();
		SCR_ArsenalComponent.OYB_RefreshAll();
	}

	// The item's price in the game's catalogue, before any override.
	static float CatalogPrice(ResourceName prefab, SCR_ArsenalComponent arsenal)
	{
		SCR_EntityCatalogManagerComponent catalogManager = SCR_EntityCatalogManagerComponent.GetInstance();
		if (!catalogManager)
			return 0;

		SCR_Faction faction;
		SCR_EArsenalSupplyCostType costType = SCR_EArsenalSupplyCostType.DEFAULT;
		if (arsenal)
		{
			faction = arsenal.GetAssignedFaction();
			costType = arsenal.GetSupplyCostType();
		}

		SCR_EntityCatalogEntry entry = catalogManager.GetEntryWithPrefabFromAnyCatalog(EEntityCatalogType.ITEM, prefab, faction);
		if (!entry)
			return 0;

		SCR_ArsenalItem data = SCR_ArsenalItem.Cast(entry.GetEntityDataOfType(SCR_ArsenalItem));
		if (!data)
			return 0;

		return data.GetSupplyCost(costType, false);
	}

	//------------------------------------------------------------------------------------------------
	// Server

	void StartServer()
	{
		if (m_bServerStarted || !Replication.IsServer())
			return;
		m_bServerStarted = true;

		FileIO.MakeDirectory(FOLDER);
		Reload();
		GetGame().GetCallqueue().CallLater(Reload, RELOAD_EVERY_MS, true);
		ListCatalogItems();
		StartScan();
	}

	// Reads settings.json, and only when its text changed applies it and sends it
	// to the players, once each. Reading a small local file once a minute costs
	// nothing on the network.
	protected void Reload()
	{
		string text = ReadFile(SETTINGS_FILE);
		if (text == m_sLoadedText)
			return;
		m_sLoadedText = text;

		array<string> hidden = {};
		array<string> priceItems = {};
		array<int> priceCosts = {};
		array<string> extraItems = {};
		array<int> extraCosts = {};
		array<string> extraFactions = {};
		array<string> extraCategories = {};
		array<string> categoryItems = {};
		array<string> categoryNames = {};
		if (!text.IsEmpty())
		{
			JsonLoadContext context = new JsonLoadContext();
			if (context.LoadFromString(text))
			{
				context.ReadValue("hidden", hidden);
				context.ReadValue("priceItems", priceItems);
				context.ReadValue("priceCosts", priceCosts);
				context.ReadValue("extraItems", extraItems);
				context.ReadValue("extraCosts", extraCosts);
				context.ReadValue("extraFactions", extraFactions);
				context.ReadValue("extraCategories", extraCategories);
				context.ReadValue("categoryItems", categoryItems);
				context.ReadValue("categoryNames", categoryNames);
			}
			else
			{
				PrintFormat("OYB_ARSENAL %1 isn't valid JSON, keeping no hidden, priced or added items", SETTINGS_FILE, level: LogLevel.WARNING);
			}
		}

		array<string> extraMeta = {};
		for (int m = 0; m < Math.Min(extraFactions.Count(), extraCategories.Count()); m++)
			extraMeta.Insert(extraFactions[m] + "|" + extraCategories[m]);

		Apply(hidden, priceItems, priceCosts, extraItems, extraCosts, extraMeta, categoryItems, categoryNames);
		PrintFormat("OYB_ARSENAL settings loaded: %1 hidden, %2 priced, %3 added, %4 moved", m_Hidden.Count(), m_Prices.Count(), m_Extras.Count(), m_Categories.Count());

		array<int> players = {};
		GetGame().GetPlayerManager().GetPlayers(players);
		foreach (int playerId : players)
		{
			SCR_PlayerController controller = SCR_PlayerController.Cast(GetGame().GetPlayerManager().GetPlayerController(playerId));
			if (controller)
				controller.OYB_SendSettings();
		}
	}

	protected string ReadFile(string path)
	{
		if (!FileIO.FileExists(path))
			return string.Empty;

		FileHandle file = FileIO.OpenFile(path, FileMode.READ);
		if (!file)
			return string.Empty;

		string text;
		string line;
		while (file.ReadLine(line) >= 0)
			text += line + "\n";
		file.Close();
		return text;
	}

	// Every item any faction's arsenal can offer in this game mode. Hidden items
	// are included so the panel can show them again.
	protected void ListCatalogItems()
	{
		m_mListed = new map<ResourceName, int>();
		m_aPrefabs = {};
		m_aNames = {};
		m_aCategories = {};
		m_aFactions = {};
		m_aPrices = {};
		m_aFound = {};

		SCR_EntityCatalogManagerComponent catalogManager = SCR_EntityCatalogManagerComponent.GetInstance();
		FactionManager factionManager = GetGame().GetFactionManager();
		if (!catalogManager || !factionManager)
		{
			Print("OYB_ARSENAL no item catalogue or factions yet, only scanned items will be listed", LogLevel.WARNING);
			return;
		}

		array<Faction> factions = {};
		factionManager.GetFactionsList(factions);
		SCR_EArsenalGameModeType gameModeType = SCR_ArsenalManagerComponent.GetArsenalGameModeType_Static();
		foreach (Faction faction : factions)
		{
			SCR_Faction scrFaction = SCR_Faction.Cast(faction);
			if (!scrFaction)
				continue;

			array<SCR_ArsenalItem> items = catalogManager.GetFilteredArsenalItems(-1, -1, gameModeType, scrFaction);
			if (!items)
				continue;

			foreach (SCR_ArsenalItem item : items)
			{
				if (!item || item.GetItemResourceName().IsEmpty())
					continue;

				ResourceName prefab = item.GetItemResourceName();
				int at;
				if (m_mListed.Find(prefab, at))
				{
					m_aFactions[at] = m_aFactions[at] + "," + scrFaction.GetFactionKey();
					continue;
				}

				int category = OYB_ArsenalCategories.Refine(OYB_ArsenalCategories.FromItem(item.GetItemType(), item.GetItemMode()), prefab);
				AddListed(prefab, category, scrFaction.GetFactionKey(), item.GetSupplyCost(SCR_EArsenalSupplyCostType.DEFAULT, false), 0);
			}
		}
	}

	protected void AddListed(ResourceName prefab, int category, string factions, int price, int found)
	{
		string name = OYB_ArsenalCatalogService.OYB_ReadInventoryName(prefab);
		if (name.IsEmpty() || name == "MISSING")
			name = FilePath.StripExtension(FilePath.StripPath(prefab.GetPath()));

		m_mListed.Insert(prefab, m_aPrefabs.Count());
		m_aPrefabs.Insert(prefab);
		m_aNames.Insert(name);
		m_aCategories.Insert(OYB_ArsenalCategories.GetName(category));
		m_aFactions.Insert(factions);
		m_aPrices.Insert(price);
		m_aFound.Insert(found);
	}

	// Every prefab in the loaded mods' Prefabs folders, so items a mod never put in
	// a faction catalogue can still be found. Base game addons are skipped: their
	// items are all catalogued.
	protected void StartScan()
	{
		m_aScanQueue = {};
		m_iScanUnusable = 0;
		m_iScanStarted = System.GetTickCount();

		array<string> addons = {};
		GameProject.GetLoadedAddons(addons);
		foreach (string guid : addons)
		{
			if (GameProject.IsVanillaAddon(guid))
				continue;

			string addonId = GameProject.GetAddonID(guid);
			if (addonId.IsEmpty())
				continue;

			int before = m_aScanQueue.Count();
			SearchResourcesFilter filter = new SearchResourcesFilter();
			filter.rootPath = "$" + addonId + ":Prefabs";
			filter.fileExtensions = {"et"};
			ResourceDatabase.SearchResources(filter, OnScanFound);
			PrintFormat("OYB_ARSENAL scan: %1 prefabs in %2", m_aScanQueue.Count() - before, GameProject.GetAddonTitle(guid));
		}

		// A bit at a time, so a big modpack doesn't hold the server up.
		GetGame().GetCallqueue().CallLater(ScanBatch, SCAN_BATCH_GAP_MS);
	}

	protected void OnScanFound(ResourceName resourceName, string exactPath = "")
	{
		if (!m_mListed.Contains(resourceName))
			m_aScanQueue.Insert(resourceName);
	}

	protected void ScanBatch()
	{
		int done;
		while (!m_aScanQueue.IsEmpty() && done < SCAN_BATCH)
		{
			ResourceName prefab = m_aScanQueue[m_aScanQueue.Count() - 1];
			m_aScanQueue.Remove(m_aScanQueue.Count() - 1);
			done++;

			if (m_mListed.Contains(prefab))
				continue;

			int category = OYB_ArsenalCategories.FromPrefab(prefab);
			if (category == -1)
				continue;
			if (category == -2)
			{
				m_iScanUnusable++;
				continue;
			}

			AddListed(prefab, category, string.Empty, 0, 1);
		}

		if (!m_aScanQueue.IsEmpty())
		{
			GetGame().GetCallqueue().CallLater(ScanBatch, SCAN_BATCH_GAP_MS);
			return;
		}

		WriteItems();
	}

	protected void WriteItems()
	{
		int found;
		foreach (int flag : m_aFound)
			found += flag;

		JsonSaveContext context = new JsonSaveContext();
		context.WriteValue("version", 2);
		context.WriteValue("prefabs", m_aPrefabs);
		context.WriteValue("names", m_aNames);
		context.WriteValue("categories", m_aCategories);
		context.WriteValue("factions", m_aFactions);
		context.WriteValue("prices", m_aPrices);
		context.WriteValue("found", m_aFound);
		if (context.SaveToFile(ITEMS_FILE))
			PrintFormat("OYB_ARSENAL wrote %1 items to %2 (%3 found by scan, %4 scanned items skipped as unusable); the scan took %5 ms spread over many frames", m_aPrefabs.Count(), ITEMS_FILE, found, m_iScanUnusable, System.GetTickCount(m_iScanStarted));
		else
			PrintFormat("OYB_ARSENAL couldn't write %1", ITEMS_FILE, level: LogLevel.WARNING);

		m_aScanQueue = null;
	}
}
