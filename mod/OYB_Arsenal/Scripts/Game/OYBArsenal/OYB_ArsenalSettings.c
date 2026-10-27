// Server settings made in the OYB panel's Arsenal page: items to hide and price
// overrides. The server reads them from its profile folder, re-reads them every
// minute so a change applies without a restart, and passes them to each player
// so the arsenal screen matches what the server enforces.
//
// $profile:OYB_Arsenal/settings.json (written by the panel):
//   { "version": 1, "hidden": [prefab, ...],
//     "priceItems": [prefab, ...], "priceCosts": [cost, ...] }
//
// The server also writes $profile:OYB_Arsenal/items.json, every item the
// arsenals can offer with its name, category, factions and default price, so the
// panel can list them (modded items included).
//
// The game classes come first in this file so their additions exist by the time
// OYB_ArsenalSettings uses them.

// Hidden items drop out of every arsenal's list, on the server and on each
// player's screen alike.
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
// and overridden prices are what's charged and refunded. The game's own checks
// run first; OYB only settles the difference to the new price afterwards.
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

		int price;
		SCR_ResourceComponent resourceComponent = SCR_ResourceComponent.Cast(Replication.FindItem(resourceComponentRplId));
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

		// The game's refund is a share of the item's catalogue price; the same
		// share of the new price is what's due.
		SCR_ArsenalComponent arsenal = SCR_ArsenalComponent.FindArsenalComponent(resourceComponent.GetOwner());
		float gameRefund = SCR_ArsenalManagerComponent.GetItemRefundAmount(item, arsenal, false);
		float catalogPrice = OYB_ArsenalSettings.CatalogPrice(item.GetPrefabData().GetPrefabName(), arsenal);

		super.RpcAsk_ArsenalRefundItem_(resourceComponentRplId, inventoryItemRplId, resourceType);

		if (item && !item.IsDeleted())
			return;
		if (gameRefund <= 0 || catalogPrice <= 0)
			return;

		float share = Math.Min(gameRefund / catalogPrice, 1);
		float delta = (price - catalogPrice) * share;
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

// The price on an arsenal item card follows the override.
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

class OYB_ArsenalSettings
{
	protected static const string SETTINGS_FILE = "$profile:OYB_Arsenal/settings.json";
	protected static const string ITEMS_FILE = "$profile:OYB_Arsenal/items.json";
	protected static const string FOLDER = "$profile:OYB_Arsenal";
	protected static const int RELOAD_EVERY_MS = 60000;

	protected static ref OYB_ArsenalSettings s_Instance;

	protected ref set<ResourceName> m_Hidden = new set<ResourceName>();
	protected ref map<ResourceName, int> m_Prices = new map<ResourceName, int>();
	protected string m_sLoadedText;
	protected bool m_bServerStarted;

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

	bool GetPrice(ResourceName prefab, out int price)
	{
		return m_Prices.Find(prefab, price);
	}

	void GetLists(notnull array<string> hidden, notnull array<string> priceItems, notnull array<int> priceCosts)
	{
		foreach (ResourceName prefab : m_Hidden)
			hidden.Insert(prefab);

		foreach (ResourceName prefab, int cost : m_Prices)
		{
			priceItems.Insert(prefab);
			priceCosts.Insert(cost);
		}
	}

	// Takes new settings and has every arsenal rebuild its list, so hidden items
	// drop out of open arsenal screens too.
	void Apply(notnull array<string> hidden, notnull array<string> priceItems, notnull array<int> priceCosts)
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
		ExportItems();
		Reload();
		GetGame().GetCallqueue().CallLater(Reload, RELOAD_EVERY_MS, true);
	}

	// Reads settings.json if it changed and sends the result to every player.
	protected void Reload()
	{
		string text = ReadFile(SETTINGS_FILE);
		if (text == m_sLoadedText)
			return;
		m_sLoadedText = text;

		array<string> hidden = {};
		array<string> priceItems = {};
		array<int> priceCosts = {};
		if (!text.IsEmpty())
		{
			JsonLoadContext context = new JsonLoadContext();
			if (context.LoadFromString(text))
			{
				context.ReadValue("hidden", hidden);
				context.ReadValue("priceItems", priceItems);
				context.ReadValue("priceCosts", priceCosts);
			}
			else
			{
				PrintFormat("OYB_ARSENAL %1 isn't valid JSON, keeping no hidden items or prices", SETTINGS_FILE, level: LogLevel.WARNING);
			}
		}

		if (priceItems.Count() != priceCosts.Count())
		{
			PrintFormat("OYB_ARSENAL %1 has %2 priced items but %3 prices, prices ignored", SETTINGS_FILE, priceItems.Count(), priceCosts.Count(), level: LogLevel.WARNING);
			priceItems.Clear();
			priceCosts.Clear();
		}

		Apply(hidden, priceItems, priceCosts);
		PrintFormat("OYB_ARSENAL settings loaded: %1 hidden, %2 priced", m_Hidden.Count(), m_Prices.Count());

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

	// Every item any faction's arsenal can offer in this game mode, for the
	// panel's list. Hidden items are included so they can be shown again.
	protected void ExportItems()
	{
		SCR_EntityCatalogManagerComponent catalogManager = SCR_EntityCatalogManagerComponent.GetInstance();
		FactionManager factionManager = GetGame().GetFactionManager();
		if (!catalogManager || !factionManager)
		{
			Print("OYB_ARSENAL no item catalogue or factions yet, items.json not written", LogLevel.WARNING);
			return;
		}

		array<Faction> factions = {};
		factionManager.GetFactionsList(factions);

		map<ResourceName, int> index = new map<ResourceName, int>();
		array<string> prefabs = {};
		array<string> names = {};
		array<string> categories = {};
		array<string> factionKeys = {};
		array<int> prices = {};

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
				if (!item)
					continue;

				ResourceName prefab = item.GetItemResourceName();
				if (prefab.IsEmpty())
					continue;

				int at;
				if (index.Find(prefab, at))
				{
					factionKeys[at] = factionKeys[at] + "," + scrFaction.GetFactionKey();
					continue;
				}

				string name = OYB_ArsenalCatalogService.OYB_ReadInventoryName(prefab);
				if (name.IsEmpty() || name == "MISSING")
					name = FilePath.StripExtension(FilePath.StripPath(prefab.GetPath()));

				index.Insert(prefab, prefabs.Count());
				prefabs.Insert(prefab);
				names.Insert(name);
				categories.Insert(OYB_ArsenalCategories.GetName(OYB_ArsenalCategories.FromItem(item.GetItemType(), item.GetItemMode())));
				factionKeys.Insert(scrFaction.GetFactionKey());
				prices.Insert(item.GetSupplyCost(SCR_EArsenalSupplyCostType.DEFAULT, false));
			}
		}

		JsonSaveContext context = new JsonSaveContext();
		context.WriteValue("version", 1);
		context.WriteValue("prefabs", prefabs);
		context.WriteValue("names", names);
		context.WriteValue("categories", categories);
		context.WriteValue("factions", factionKeys);
		context.WriteValue("prices", prices);
		if (context.SaveToFile(ITEMS_FILE))
			PrintFormat("OYB_ARSENAL wrote %1 items to %2", prefabs.Count(), ITEMS_FILE);
		else
			PrintFormat("OYB_ARSENAL couldn't write %1", ITEMS_FILE, level: LogLevel.WARNING);
	}
}
