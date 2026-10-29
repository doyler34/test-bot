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
//     "categoryItems": [prefab, ...], "categoryNames": [category name, ...],
//     "sideItems": [prefab, ...], "sideFactions": ["US,USSR", ...],
//     "renameItems": [prefab, ...], "renameNames": [new name, ...],
//     "refundCap": most supplies one refund gives back (350 if left out),
//     "refundItems": [prefab, ...], "refundCosts": [supplies given back, ...] }
//
// categoryItems moves catalogued items to another category in the arsenal.
// sideItems gives a catalogued item to exactly those factions' arsenals, whatever
// faction its mod tagged it with; a faction whose catalogue doesn't list it gets
// it the way an added item is given out. renameItems gives items a new name on
// every player's screen (OYB_ArsenalItemNames.c).
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
			if (item && (settings.IsHidden(item.GetItemResourceName()) || !settings.IsOnSide(item.GetItemResourceName(), this)))
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

	// The game only lets a kit be saved (as the respawn kit) when every item in
	// it is in the player's faction catalogue, or in the arsenal's storage,
	// depending on the arsenal. Items OYB hands out at that arsenal (added
	// items, and items the panel put on a side whose catalogue doesn't list
	// them) are neither, so a kit with one was always refused. Those count as
	// allowed here; every other item, the blacklist, the rank and the supply
	// allocation are checked as the game checks them.
	override protected bool CanSaveLoadout(int playerId, notnull GameEntity characterEntity, Faction characterFaction, SCR_ArsenalComponent arsenalComponent, bool sendNotificationOnFailed)
	{
		if (super.CanSaveLoadout(playerId, characterEntity, characterFaction, arsenalComponent, false))
			return true;

		if (arsenalComponent && OYB_CanSaveWithOYBItems(playerId, characterEntity, characterFaction, arsenalComponent))
			return true;

		return super.CanSaveLoadout(playerId, characterEntity, characterFaction, arsenalComponent, sendNotificationOnFailed);
	}

	protected bool OYB_CanSaveWithOYBItems(int playerId, notnull GameEntity characterEntity, Faction characterFaction, notnull SCR_ArsenalComponent arsenalComponent)
	{
		array<ResourceName> offered = {};
		OYB_ArsenalSettings.Get().GetExtrasFor(arsenalComponent, offered);
		if (offered.IsEmpty())
			return false;

		SCR_EArsenalSaveType saveType = arsenalComponent.GetArsenalSaveType();
		if (saveType == SCR_EArsenalSaveType.SAVING_DISABLED)
			return false;

		SCR_Faction playerFaction = SCR_Faction.Cast(characterFaction);
		if (playerFaction && !playerFaction.IsCustomLoadoutSupported())
			return false;

		SCR_InventoryStorageManagerComponent characterInventory = SCR_InventoryStorageManagerComponent.Cast(characterEntity.FindComponent(SCR_InventoryStorageManagerComponent));
		if (!characterInventory)
			return false;

		array<SCR_EntityCatalog> itemCatalogs = {};
		SCR_ArsenalInventoryStorageManagerComponent arsenalInventory;
		if (saveType == SCR_EArsenalSaveType.FACTION_ITEMS_ONLY && playerFaction)
		{
			SCR_EntityCatalog ownCatalog = playerFaction.GetFactionEntityCatalogOfType(EEntityCatalogType.ITEM, false);
			if (ownCatalog)
				itemCatalogs.Insert(ownCatalog);
		}
		else if (saveType == SCR_EArsenalSaveType.FRIENDLY_AND_FACTION_ITEMS_ONLY && playerFaction)
		{
			array<Faction> friendlyFactions = {};
			playerFaction.GetFriendlyFactions(friendlyFactions);
			foreach (Faction friendly : friendlyFactions)
			{
				SCR_Faction scrFriendly = SCR_Faction.Cast(friendly);
				if (!scrFriendly)
					continue;

				SCR_EntityCatalog friendlyCatalog = scrFriendly.GetFactionEntityCatalogOfType(EEntityCatalogType.ITEM, false);
				if (friendlyCatalog)
					itemCatalogs.Insert(friendlyCatalog);
			}
		}
		else if (saveType == SCR_EArsenalSaveType.IN_ARSENAL_ITEMS_ONLY)
		{
			arsenalInventory = arsenalComponent.GetArsenalInventoryComponent();
		}

		array<IEntity> items = {};
		characterInventory.GetItems(items, EStoragePurpose.PURPOSE_DEPOSIT | EStoragePurpose.PURPOSE_WEAPON_PROXY);
		set<ResourceName> checked = new set<ResourceName>();
		SCR_ECharacterRank requiredRank = SCR_ECharacterRank.RENEGADE;
		foreach (IEntity item : items)
		{
			EntityPrefabData prefabData = item.GetPrefabData();
			if (!prefabData)
				continue;

			ResourceName prefab = prefabData.GetPrefabName();
			if (prefab.IsEmpty() || !checked.Insert(prefab))
				continue;

			if (m_LoadoutSaveBlackListHolder && m_LoadoutSaveBlackListHolder.IsPrefabBlacklisted(prefab))
				return false;

			if (offered.Contains(prefab))
				continue;

			if (arsenalInventory)
			{
				if (!arsenalInventory.IsPrefabInArsenalStorage(prefab))
					return false;
				continue;
			}

			if (itemCatalogs.IsEmpty())
				continue;

			SCR_EntityCatalogEntry entry = null;
			foreach (SCR_EntityCatalog catalog : itemCatalogs)
			{
				entry = catalog.GetEntryWithPrefab(prefab);
				if (entry)
					break;
			}
			if (!entry)
				return false;

			SCR_ArsenalItem arsenalItem = SCR_ArsenalItem.Cast(entry.GetEntityDataOfType(SCR_ArsenalItem));
			if (arsenalItem && AreItemsRankLocked())
				requiredRank = Math.Max(requiredRank, arsenalItem.GetRequiredRank());
		}

		SCR_PlayerController playerController = SCR_PlayerController.Cast(GetGame().GetPlayerManager().GetPlayerController(playerId));
		if (playerController && AreItemsRankLocked())
		{
			SCR_PlayerXPHandlerComponent xpHandler = SCR_PlayerXPHandlerComponent.Cast(playerController.FindComponent(SCR_PlayerXPHandlerComponent));
			if (xpHandler && xpHandler.GetPlayerRankByXP() < requiredRank)
				return false;
		}

		if (m_bUseMilitarySupplyAllocation && playerController)
		{
			SCR_PlayerSupplyAllocationComponent allocation = SCR_PlayerSupplyAllocationComponent.Cast(playerController.FindComponent(SCR_PlayerSupplyAllocationComponent));
			if (allocation && GetCharacterLoadoutSupplyCost(characterEntity, false) > allocation.GetPlayerMilitarySupplyAllocation())
				return false;
		}

		return true;
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
		SCR_ArsenalComponent requestedFrom;
		if (resourceComponent)
			requestedFrom = SCR_ArsenalComponent.FindArsenalComponent(resourceComponent.GetOwner());
		if (requestedFrom && !settings.IsOnSide(resourceNameItem, requestedFrom))
		{
			PrintFormat("OYB_ARSENAL refused an item this side doesn't get: %1", FilePath.StripPath(resourceNameItem.GetPath()), level: LogLevel.WARNING);
			return;
		}

		if (settings.IsExtra(resourceNameItem) || settings.IsAddedAt(resourceNameItem, requestedFrom))
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
		OYB_ArsenalSettings.Get().GetPriceAt(prefab, arsenal, price);
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
		// One refund, however much is packed in what's handed back, gives at most
		// the panel's refund limit (see OYB_Refunds).
		OYB_Refunds.Start(OYB_ArsenalSettings.Get().GetRefundCap());

		InventoryItemComponent itemComponent = InventoryItemComponent.Cast(Replication.FindItem(inventoryItemRplId));
		SCR_ResourceComponent resourceComponent = SCR_ResourceComponent.Cast(Replication.FindItem(resourceComponentRplId));
		IEntity item;
		if (itemComponent)
			item = itemComponent.GetOwner();

		SCR_ArsenalComponent arsenal;
		if (resourceComponent)
			arsenal = SCR_ArsenalComponent.FindArsenalComponent(resourceComponent.GetOwner());

		// A refund set in the panel is what the item gives back, whatever the
		// game would have given for it (fitted parts and contents included).
		int refundSet;
		if (item && item.GetPrefabData() && resourceComponent && resourceType == EResourceType.SUPPLIES && OYB_ArsenalSettings.Get().GetRefund(item.GetPrefabData().GetPrefabName(), refundSet))
		{
			OYB_Refunds.SetFixed(refundSet);
			super.RpcAsk_ArsenalRefundItem_(resourceComponentRplId, inventoryItemRplId, resourceType);

			// The game gives nothing for an item it has no price for; then the
			// set refund is given here, once the item has gone.
			float notGiven = OYB_Refunds.TakeFixed();
			if (notGiven > 0 && (!item || item.IsDeleted()))
			{
				SCR_ResourceGenerator refundGenerator = resourceComponent.GetGenerator(EResourceGeneratorID.DEFAULT, resourceType);
				float allowedRefund = OYB_Refunds.Take(refundGenerator, notGiven);
				if (refundGenerator && allowedRefund > 0)
					refundGenerator.RequestGeneration(allowedRefund);
			}

			OYB_Refunds.Stop();
			return;
		}

		int price;
		if (!item || !item.GetPrefabData() || !resourceComponent || resourceType != EResourceType.SUPPLIES || !OYB_ArsenalSettings.Get().GetPriceAt(item.GetPrefabData().GetPrefabName(), arsenal, price))
		{
			super.RpcAsk_ArsenalRefundItem_(resourceComponentRplId, inventoryItemRplId, resourceType);
			OYB_Refunds.Stop();
			return;
		}

		ResourceName prefab = item.GetPrefabData().GetPrefabName();
		float gameRefund = SCR_ArsenalManagerComponent.GetItemRefundAmount(item, arsenal, false);
		float catalogPrice = OYB_ArsenalSettings.CatalogPrice(prefab, arsenal);
		bool extra = OYB_ArsenalSettings.Get().IsExtra(prefab) || OYB_ArsenalSettings.Get().IsAddedAt(prefab, arsenal);

		super.RpcAsk_ArsenalRefundItem_(resourceComponentRplId, inventoryItemRplId, resourceType);

		if ((item && !item.IsDeleted()) || !arsenal || !arsenal.IsArsenalUsingSupplies())
		{
			OYB_Refunds.Stop();
			return;
		}

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
		OYB_Refunds.Stop();
	}

	// What the game hands back during a refund is cut to the refund limit and to
	// the room the arsenal's storage has; the rest is thrown away.
	override protected bool TryPerformResourceGeneration(notnull SCR_ResourceActor actor, float resourceValue)
	{
		if (!OYB_Refunds.IsOn())
			return super.TryPerformResourceGeneration(actor, resourceValue);

		float setRefund = OYB_Refunds.TakeFixed();
		if (setRefund >= 0)
			resourceValue = setRefund;

		float allowed = OYB_Refunds.Take(SCR_ResourceGenerator.Cast(actor), resourceValue);
		if (allowed <= 0)
			return true;

		return super.TryPerformResourceGeneration(actor, allowed);
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
			// Only a refund is held to the limit; giving back the difference on a
			// purchase stays as it was.
			SCR_ResourceGenerator generator = resourceComponent.GetGenerator(EResourceGeneratorID.DEFAULT, EResourceType.SUPPLIES);
			float allowed = -amount;
			if (OYB_Refunds.IsOn())
				allowed = OYB_Refunds.Take(generator, allowed);
			if (generator && allowed > 0)
				generator.RequestGeneration(allowed);
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

		SCR_ArsenalComponent arsenal;
		if (m_ArsenalResourceComponent)
			arsenal = SCR_ArsenalComponent.FindArsenalComponent(m_ArsenalResourceComponent.GetOwner());

		int price;
		if (!OYB_ArsenalSettings.Get().GetPriceAt(m_pItem.GetOwner().GetPrefabData().GetPrefabName(), arsenal, price))
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

// Keeps refunds in check. Packing a backpack full of gear and handing it to an
// arsenal would otherwise turn into a pile of supplies, so a refund (whatever
// is inside the item) gives back at most the panel's limit. What the arsenal's
// storage has no room for is thrown away instead of being dropped next to it
// as supply crates.
class OYB_Refunds
{
	protected static bool s_bOn;
	protected static float s_fLeft;
	protected static float s_fStartedAt;
	protected static float s_fFixed = -1;

	static void Start(float limit)
	{
		s_bOn = true;
		s_fLeft = limit;
		s_fFixed = -1;
		s_fStartedAt = Now();
	}

	// The refund set in the panel for the item being handed back, used in place
	// of the game's own amount.
	static void SetFixed(float amount)
	{
		s_fFixed = amount;
	}

	// The set refund, once; -1 if there's none or it's been used.
	static float TakeFixed()
	{
		float amount = s_fFixed;
		s_fFixed = -1;
		return amount;
	}

	static void Stop()
	{
		s_bOn = false;
	}

	// Only for the refund in hand: if a script error ever skipped Stop, the
	// limit still lapses with the moment it was started in, so nothing else
	// that hands out supplies is ever cut.
	static bool IsOn()
	{
		return s_bOn && s_fStartedAt == Now();
	}

	protected static float Now()
	{
		if (!GetGame().GetWorld())
			return -1;

		return GetGame().GetWorld().GetWorldTime();
	}

	// How much of amount may be given back now: within what's left of the limit
	// (while a refund is on) and the room the generator's storage has.
	static float Take(SCR_ResourceGenerator generator, float amount)
	{
		bool on = IsOn();
		float allowed = amount;
		if (on)
			allowed = Math.Min(allowed, s_fLeft);
		if (generator)
			allowed = Math.Min(allowed, generator.GetAggregatedMaxResourceValue() - generator.GetAggregatedResourceValue());
		allowed = Math.Max(allowed, 0);

		if (on)
			s_fLeft -= allowed;
		if (allowed < amount)
			PrintFormat("OYB_ARSENAL refund cut from %1 to %2 (limit or no room)", Math.Round(amount), Math.Round(allowed));
		return allowed;
	}
}

// While a refund is on, supplies with nowhere to go are thrown away rather than
// dropped as crates.
modded class SCR_ResourceGeneratorActionDropContainers
{
	override void PerformAction(notnull SCR_ResourceGenerator generator, inout float resourceValue)
	{
		if (OYB_Refunds.IsOn())
		{
			resourceValue = 0;
			return;
		}

		super.PerformAction(generator, resourceValue);
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
	protected ref map<ResourceName, ref array<string>> m_Sides = new map<ResourceName, ref array<string>>();
	protected ref map<ResourceName, ref OYB_ArsenalExtra> m_SideExtras = new map<ResourceName, ref OYB_ArsenalExtra>();
	protected ref map<ResourceName, string> m_Renames = new map<ResourceName, string>();
	protected static const int DEFAULT_REFUND_CAP = 350;
	protected int m_iRefundCap = DEFAULT_REFUND_CAP;
	protected ref map<ResourceName, int> m_Refunds = new map<ResourceName, int>();
	protected string m_sLoadedText;
	protected string m_sRefusedText;
	protected bool m_bHaveSettings;
	protected bool m_bUnreadableLogged;
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

	// Also the catalogue price of an item put on a side whose catalogue doesn't
	// list it.
	bool GetPriceAt(ResourceName prefab, SCR_ArsenalComponent arsenal, out int price)
	{
		if (GetPrice(prefab, price))
			return true;
		if (!IsAddedAt(prefab, arsenal))
			return false;

		price = GetSideExtra(prefab).m_iCost;
		return true;
	}

	protected static string FactionKey(SCR_ArsenalComponent arsenal)
	{
		if (!arsenal || !arsenal.GetAssignedFaction())
			return string.Empty;

		return arsenal.GetAssignedFaction().GetFactionKey();
	}

	// False when the panel gave the item to other sides only. Arsenals with no
	// faction keep everything.
	bool IsOnSide(ResourceName prefab, SCR_ArsenalComponent arsenal)
	{
		array<string> sides = m_Sides.Get(prefab);
		if (!sides)
			return true;

		string factionKey = FactionKey(arsenal);
		return factionKey.IsEmpty() || sides.Contains(factionKey);
	}

	// The panel put the item on this arsenal's side, but the side's catalogue
	// doesn't list it, so OYB hands it out like an added item.
	bool IsAddedAt(ResourceName prefab, SCR_ArsenalComponent arsenal)
	{
		array<string> sides = m_Sides.Get(prefab);
		if (!sides || !arsenal || m_Hidden.Contains(prefab) || m_Extras.Contains(prefab))
			return false;

		SCR_Faction faction = arsenal.GetAssignedFaction();
		if (!faction || !sides.Contains(faction.GetFactionKey()))
			return false;

		SCR_EntityCatalogManagerComponent catalogManager = SCR_EntityCatalogManagerComponent.GetInstance();
		if (!catalogManager || catalogManager.GetEntryWithPrefabFromFactionCatalog(EEntityCatalogType.ITEM, prefab, faction) || catalogManager.GetEntryWithPrefabFromCatalog(EEntityCatalogType.ITEM, prefab))
			return false;

		return GetSideExtra(prefab) != null;
	}

	// Price and category from whichever faction's catalogue lists the item.
	protected OYB_ArsenalExtra GetSideExtra(ResourceName prefab)
	{
		OYB_ArsenalExtra extra = m_SideExtras.Get(prefab);
		if (extra || m_SideExtras.Contains(prefab))
			return extra;

		SCR_EntityCatalogManagerComponent catalogManager = SCR_EntityCatalogManagerComponent.GetInstance();
		SCR_EntityCatalogEntry entry;
		if (catalogManager)
			entry = catalogManager.GetEntryWithPrefabFromAnyCatalog(EEntityCatalogType.ITEM, prefab);
		SCR_ArsenalItem data;
		if (entry)
			data = SCR_ArsenalItem.Cast(entry.GetEntityDataOfType(SCR_ArsenalItem));
		if (!data)
		{
			m_SideExtras.Insert(prefab, null);
			return null;
		}

		extra = new OYB_ArsenalExtra();
		extra.m_sPrefab = prefab;
		extra.m_iCost = data.GetSupplyCost(SCR_EArsenalSupplyCostType.DEFAULT, false);
		int moved;
		if (m_Categories.Find(prefab, moved))
			extra.m_iCategory = moved;
		else
			extra.m_iCategory = OYB_ArsenalCategories.Refine(OYB_ArsenalCategories.FromItem(data.GetItemType(), data.GetItemMode()), prefab);
		m_SideExtras.Insert(prefab, extra);
		return extra;
	}

	// The added items this arsenal offers: its faction, and the kinds of item it
	// was set up to carry.
	void GetExtrasFor(SCR_ArsenalComponent arsenal, notnull array<ResourceName> extras)
	{
		if ((m_aExtraOrder.IsEmpty() && m_Sides.IsEmpty()) || !arsenal)
			return;

		// The game switches an arsenal off by clearing its item modes; an arsenal
		// that's off offers nothing, added items included.
		if (!arsenal.IsArsenalEnabled() || arsenal.GetSupportedArsenalItemModes() == 0)
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

		foreach (ResourceName sidePrefab, array<string> sides : m_Sides)
		{
			if (!IsAddedAt(sidePrefab, arsenal))
				continue;
			if (OYB_ArsenalCategories.ToArsenalTypes(GetSideExtra(sidePrefab).m_iCategory) & supported)
				extras.Insert(sidePrefab);
		}
	}

	// A category set in the panel for a catalogued item.
	bool GetCategory(ResourceName prefab, out int category)
	{
		return m_Categories.Find(prefab, category);
	}

	OYB_ArsenalExtra GetExtra(ResourceName prefab)
	{
		OYB_ArsenalExtra extra = m_Extras.Get(prefab);
		if (!extra && m_Sides.Contains(prefab))
			extra = GetSideExtra(prefab);

		return extra;
	}

	// Sent apart from the other lists, as one network message carries eight at
	// most. Takes effect with the Apply that follows.
	void GetSides(notnull array<string> items, notnull array<string> factions)
	{
		foreach (ResourceName prefab, array<string> sides : m_Sides)
		{
			items.Insert(prefab);
			factions.Insert(SCR_StringHelper.Join(",", sides));
		}
	}

	// A refund set in the panel for this item, in place of the game's.
	bool GetRefund(ResourceName prefab, out int refund)
	{
		return m_Refunds.Find(prefab, refund);
	}

	// The most one refund (or one loadout swap) gives back, from the panel.
	int GetRefundCap()
	{
		return m_iRefundCap;
	}

	// The name players see: the one set in the panel, else the item's own.
	string GetDisplayName(ResourceName prefab)
	{
		string name;
		if (m_Renames.Find(prefab, name))
			return name;

		return OYB_ArsenalCatalogService.OYB_ReadInventoryName(prefab);
	}

	void GetNames(notnull array<string> items, notnull array<string> names)
	{
		foreach (ResourceName prefab, string name : m_Renames)
		{
			items.Insert(prefab);
			names.Insert(name);
		}
	}

	void SetNames(notnull array<string> items, notnull array<string> names)
	{
		m_Renames.Clear();
		int count = Math.Min(items.Count(), names.Count());
		for (int i = 0; i < count; i++)
		{
			string name = names[i].Trim();
			if (!items[i].IsEmpty() && !name.IsEmpty())
				m_Renames.Set(items[i], name);
		}

		OYB_ItemNames.Set(m_Renames);
	}

	void SetSides(notnull array<string> items, notnull array<string> factions)
	{
		m_Sides.Clear();
		int count = Math.Min(items.Count(), factions.Count());
		for (int i = 0; i < count; i++)
		{
			array<string> sides = {};
			factions[i].Split(",", sides, true);
			if (!items[i].IsEmpty() && !sides.IsEmpty())
				m_Sides.Set(items[i], sides);
		}
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

		m_SideExtras.Clear();
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

	// Reads settings.json, and only when its text changed and is valid applies it
	// and sends it to the players, once each. A file that can't be read, isn't
	// valid JSON or has lists that don't pair up (one half-written, say) is
	// refused and logged, and the settings in force stay. No file at all is a
	// server without settings yet. Reading a small local file once a minute
	// costs nothing on the network.
	protected void Reload()
	{
		bool exists;
		string text;
		if (!ReadFile(SETTINGS_FILE, exists, text))
		{
			if (!m_bUnreadableLogged)
				PrintFormat("OYB_ARSENAL %1 couldn't be read, keeping the settings in force", SETTINGS_FILE, level: LogLevel.WARNING);
			m_bUnreadableLogged = true;
			return;
		}
		m_bUnreadableLogged = false;

		if (m_bHaveSettings && text == m_sLoadedText)
			return;
		if (!text.IsEmpty() && text == m_sRefusedText)
			return;

		array<string> hidden = {};
		array<string> priceItems = {};
		array<int> priceCosts = {};
		array<string> extraItems = {};
		array<int> extraCosts = {};
		array<string> extraFactions = {};
		array<string> extraCategories = {};
		array<string> categoryItems = {};
		array<string> categoryNames = {};
		array<string> sideItems = {};
		array<string> sideFactions = {};
		array<string> renameItems = {};
		array<string> renameNames = {};
		int refundCap = DEFAULT_REFUND_CAP;
		array<string> refundItems = {};
		array<int> refundCosts = {};
		if (exists)
		{
			string trimmed = text.Trim();
			string error;
			JsonLoadContext context = new JsonLoadContext();
			if (trimmed.IsEmpty())
				error = "empty file";
			else if (!context.LoadFromString(text))
				error = "not valid JSON";

			if (error.IsEmpty())
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
				context.ReadValue("sideItems", sideItems);
				context.ReadValue("sideFactions", sideFactions);
				context.ReadValue("renameItems", renameItems);
				context.ReadValue("renameNames", renameNames);
				context.ReadValue("refundItems", refundItems);
				context.ReadValue("refundCosts", refundCosts);
				if (!context.ReadValue("refundCap", refundCap) || refundCap < 0)
					refundCap = DEFAULT_REFUND_CAP;

				if (priceItems.Count() != priceCosts.Count())
					error = "priceItems and priceCosts differ in length";
				else if (extraItems.Count() != extraCosts.Count() || extraItems.Count() != extraFactions.Count() || extraItems.Count() != extraCategories.Count())
					error = "the extra item lists differ in length";
				else if (categoryItems.Count() != categoryNames.Count())
					error = "categoryItems and categoryNames differ in length";
				else if (sideItems.Count() != sideFactions.Count())
					error = "sideItems and sideFactions differ in length";
				else if (renameItems.Count() != renameNames.Count())
					error = "renameItems and renameNames differ in length";
				else if (refundItems.Count() != refundCosts.Count())
					error = "refundItems and refundCosts differ in length";
			}

			if (!error.IsEmpty())
			{
				m_sRefusedText = text;
				PrintFormat("OYB_ARSENAL %1 refused (%2), keeping the settings in force", SETTINGS_FILE, error, level: LogLevel.WARNING);
				return;
			}
		}

		m_bHaveSettings = true;
		m_sLoadedText = text;
		m_sRefusedText = string.Empty;

		array<string> extraMeta = {};
		for (int m = 0; m < Math.Min(extraFactions.Count(), extraCategories.Count()); m++)
			extraMeta.Insert(extraFactions[m] + "|" + extraCategories[m]);

		SetSides(sideItems, sideFactions);
		SetNames(renameItems, renameNames);
		m_iRefundCap = refundCap;
		m_Refunds.Clear();
		for (int r = 0; r < Math.Min(refundItems.Count(), refundCosts.Count()); r++)
		{
			if (!refundItems[r].IsEmpty() && refundCosts[r] >= 0)
				m_Refunds.Set(refundItems[r], refundCosts[r]);
		}
		Apply(hidden, priceItems, priceCosts, extraItems, extraCosts, extraMeta, categoryItems, categoryNames);
		PrintFormat("OYB_ARSENAL settings loaded: %1 hidden, %2 priced, %3 added, %4 moved, %5 on changed sides, %6 renamed, %8 with a set refund, refunds up to %7", m_Hidden.Count(), m_Prices.Count(), m_Extras.Count(), m_Categories.Count(), m_Sides.Count(), m_Renames.Count(), m_iRefundCap, m_Refunds.Count());

		array<int> players = {};
		GetGame().GetPlayerManager().GetPlayers(players);
		foreach (int playerId : players)
		{
			SCR_PlayerController controller = SCR_PlayerController.Cast(GetGame().GetPlayerManager().GetPlayerController(playerId));
			if (controller)
				controller.OYB_SendSettings();
		}
	}

	// False when the file is there but can't be opened.
	protected static bool ReadFile(string path, out bool exists, out string text)
	{
		text = string.Empty;
		exists = FileIO.FileExists(path);
		if (!exists)
			return true;

		FileHandle file = FileIO.OpenFile(path, FileMode.READ);
		if (!file)
			return false;

		string line;
		while (file.ReadLine(line) >= 0)
			text += line + "\n";
		file.Close();
		return true;
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
