// Saved loadouts: five slots per player, kept by the server in its profile folder
// so a client can't hand it a made-up kit. Saving writes the player's kit with the
// game's own loadout format (the one used for respawning with an arsenal kit).
// Loading at an arsenal only pays for what the player doesn't already carry, at
// the arsenal's price, or the catalogue's for items it doesn't sell. Whatever the
// kit throws away is refunded the way the arsenal refunds it when an item is put
// back.
//
// The kit last saved or loaded also becomes the player's respawn kit, through the
// game's own saved arsenal loadout (what the arsenal's "Save loadout" action
// does), so the respawn screen offers it wherever the game mode supports that.
modded class SCR_PlayerController
{
	static const int OYB_LOADOUT_SLOTS = 5;
	protected static const string OYB_LOADOUT_FOLDER = "$profile:OYB_Arsenal";
	protected static const float OYB_MAX_ARSENAL_DISTANCE_SQ = 900;
	protected static const int OYB_REQUEST_COOLDOWN_MS = 1000;

	protected float m_fOYB_LastRequestTime = -OYB_REQUEST_COOLDOWN_MS;

	protected static const int OYB_QUESTION_COOLDOWN_MS = 5000;
	protected float m_fOYB_LastSettingsQuestion = -OYB_QUESTION_COOLDOWN_MS;
	protected float m_fOYB_LastNamesQuestion = -OYB_QUESTION_COOLDOWN_MS;
	// Per controller, so joining another server asks again.
	protected bool m_bOYB_SettingsAsked;
	protected bool m_bOYB_NamesAsked;

	// What the local player sees in the slot list, sent by the server.
	protected static ref array<string> s_aOYB_LoadoutNames = {};
	protected static ref ScriptInvokerVoid s_OYB_OnLoadoutNames;

	static array<string> OYB_GetLoadoutNames()
	{
		return s_aOYB_LoadoutNames;
	}

	static ScriptInvokerVoid OYB_GetOnLoadoutNames()
	{
		if (!s_OYB_OnLoadoutNames)
			s_OYB_OnLoadoutNames = new ScriptInvokerVoid();

		return s_OYB_OnLoadoutNames;
	}

	//------------------------------------------------------------------------------------------------
	// Asked for by the arsenal panel

	// Asked once per session; after that the server sends the names whenever a
	// save changes them.
	void OYB_RequestLoadoutNames()
	{
		if (m_bOYB_NamesAsked)
			return;
		m_bOYB_NamesAsked = true;
		Rpc(OYB_RpcAsk_LoadoutNames);
	}

	void OYB_SaveLoadout(int slot, RplId arsenalResourceId)
	{
		Rpc(OYB_RpcAsk_SaveLoadout, slot, arsenalResourceId);
	}

	void OYB_LoadLoadout(int slot, RplId arsenalResourceId)
	{
		Rpc(OYB_RpcAsk_LoadLoadout, slot, arsenalResourceId);
	}

	// The panel's hidden, priced and added items (see OYB_ArsenalSettings.c).
	// Asked once per session, the first time an arsenal is opened; after that the
	// server only sends them when the panel changes something. The host shares the
	// server's copy and never asks.
	void OYB_RequestSettings()
	{
		if (m_bOYB_SettingsAsked || (OYB_IsLocal() && Replication.IsServer()))
			return;
		m_bOYB_SettingsAsked = true;
		Rpc(OYB_RpcAsk_Settings);
	}

	[RplRpc(RplChannel.Reliable, RplRcver.Server)]
	protected void OYB_RpcAsk_Settings()
	{
		if (OYB_TooSoon(m_fOYB_LastSettingsQuestion))
			return;

		OYB_SendSettings();
	}

	void OYB_SendSettings()
	{
		if (OYB_IsLocal())
			return;

		array<string> hidden = {};
		array<string> priceItems = {};
		array<int> priceCosts = {};
		array<string> extraItems = {};
		array<int> extraCosts = {};
		array<string> extraFactions = {};
		array<string> extraCategories = {};
		OYB_ArsenalSettings.Get().GetLists(hidden, priceItems, priceCosts, extraItems, extraCosts, extraFactions, extraCategories);
		Rpc(OYB_RpcDo_Settings, hidden, priceItems, priceCosts, extraItems, extraCosts, extraFactions, extraCategories);
	}

	[RplRpc(RplChannel.Reliable, RplRcver.Owner)]
	protected void OYB_RpcDo_Settings(array<string> hidden, array<string> priceItems, array<int> priceCosts, array<string> extraItems, array<int> extraCosts, array<string> extraFactions, array<string> extraCategories)
	{
		OYB_ArsenalSettings.Get().Apply(hidden, priceItems, priceCosts, extraItems, extraCosts, extraFactions, extraCategories);
	}

	// Questions a client asks once per session; a client asking again within a
	// few seconds gets nothing, so a modified one can't make the server spam.
	protected bool OYB_TooSoon(inout float lastAsked)
	{
		float now = GetGame().GetWorld().GetWorldTime();
		if (now - lastAsked < OYB_QUESTION_COOLDOWN_MS)
			return true;

		lastAsked = now;
		return false;
	}

	//------------------------------------------------------------------------------------------------
	// Server

	[RplRpc(RplChannel.Reliable, RplRcver.Server)]
	protected void OYB_RpcAsk_LoadoutNames()
	{
		if (OYB_TooSoon(m_fOYB_LastNamesQuestion))
			return;

		array<string> names = {};
		array<string> loadouts = {};
		OYB_ReadSlots(names, loadouts);
		OYB_SendNames(names);
	}

	[RplRpc(RplChannel.Reliable, RplRcver.Server)]
	protected void OYB_RpcAsk_SaveLoadout(int slot, RplId arsenalResourceId)
	{
		SCR_ChimeraCharacter character = OYB_GetLoadoutCharacter(slot);
		if (!character)
			return;

		string loadout = OYB_WriteKit(character);
		if (loadout.IsEmpty())
		{
			OYB_SendMessage("Couldn't save this kit.");
			return;
		}

		array<string> names = {};
		array<string> loadouts = {};
		OYB_ReadSlots(names, loadouts);
		names[slot] = OYB_KitName(character);
		loadouts[slot] = loadout;

		if (!OYB_WriteSlots(names, loadouts))
		{
			OYB_SendMessage("Couldn't write the loadout file.");
			return;
		}

		PrintFormat("OYB_ARSENAL loadout %1 saved for player %2: %3", slot + 1, GetPlayerId(), names[slot]);
		OYB_SendNames(names);
		OYB_SendMessage(string.Format("Saved to loadout %1.", slot + 1));

		SCR_ArsenalComponent arsenal = OYB_FindArsenalInReach(character, arsenalResourceId);
		if (arsenal)
			OYB_SetRespawnKit(arsenal);
	}

	[RplRpc(RplChannel.Reliable, RplRcver.Server)]
	protected void OYB_RpcAsk_LoadLoadout(int slot, RplId arsenalResourceId)
	{
		SCR_ChimeraCharacter character = OYB_GetLoadoutCharacter(slot);
		if (!character)
			return;

		SCR_ResourceComponent resourceComponent = SCR_ResourceComponent.Cast(Replication.FindItem(arsenalResourceId));
		if (!resourceComponent || !resourceComponent.GetOwner())
			return;

		SCR_ArsenalComponent arsenal = OYB_FindArsenalInReach(character, arsenalResourceId);
		if (!arsenal)
		{
			PrintFormat("OYB_ARSENAL player %1 tried to load a kit away from an arsenal", GetPlayerId(), level: LogLevel.WARNING);
			return;
		}

		array<string> names = {};
		array<string> loadouts = {};
		OYB_ReadSlots(names, loadouts);
		string saved = loadouts[slot];
		if (saved.IsEmpty())
		{
			OYB_SendMessage(string.Format("Slot %1 is empty.", slot + 1));
			return;
		}

		JsonLoadContext check = new JsonLoadContext();
		string factionKey;
		if (!check.LoadFromString(saved) || !check.ReadValue(SCR_PlayerArsenalLoadout.ARSENALLOADOUT_FACTION_KEY, factionKey) || factionKey != character.GetFaction().GetFactionKey())
		{
			OYB_SendMessage("That kit was saved for another faction.");
			return;
		}

		// What the kit needs that the player hasn't got, and what it drops.
		map<ResourceName, int> wanted = new map<ResourceName, int>();
		map<ResourceName, int> carried = new map<ResourceName, int>();
		OYB_CountPrefabs(saved, wanted);
		OYB_CountPrefabs(OYB_WriteKit(character), carried);

		// Items from a mod that's no longer loaded can't be spawned, and the game
		// would stop halfway through the kit after the supplies were taken.
		array<string> vanished = {};
		foreach (ResourceName kitPrefab, int kitCount : wanted)
		{
			Resource kitResource = Resource.Load(kitPrefab);
			if (!kitResource || !kitResource.IsValid())
				vanished.Insert(FilePath.StripExtension(FilePath.StripPath(kitPrefab.GetPath())));
		}
		if (!vanished.IsEmpty())
		{
			string vanishedList = SCR_StringHelper.Join(", ", vanished);
			PrintFormat("OYB_ARSENAL loadout %1 has items that no longer exist: %2", slot + 1, vanishedList, level: LogLevel.WARNING);
			OYB_SendMessage(string.Format("Loadout %1 can't load. These items no longer exist (a removed mod?): %2. Save the slot again.", slot + 1, vanishedList));
			return;
		}

		map<ResourceName, float> prices = new map<ResourceName, float>();
		array<SCR_ArsenalItem> offered = {};
		arsenal.GetFilteredArsenalItems(offered);
		foreach (SCR_ArsenalItem item : offered)
		{
			if (item)
				prices.Set(item.GetItemResourceName(), item.GetSupplyCost(arsenal.GetSupplyCostType()));
		}

		OYB_ArsenalSettings settings = OYB_ArsenalSettings.Get();

		// Parts that come fitted to something being bought, like the M203 on an
		// M16A2 M203, arrive with it, so they aren't paid for again.
		map<ResourceName, int> missingCounts = new map<ResourceName, int>();
		foreach (ResourceName prefab, int count : wanted)
		{
			if (count > carried.Get(prefab))
				missingCounts.Set(prefab, count - carried.Get(prefab));
		}
		map<ResourceName, int> comesWith = OYB_CountFittedParts(missingCounts);

		// Hidden items can't be taken from an arsenal, so a kit can't bring them in
		// either; ones already carried can stay.
		array<string> refused = {};
		foreach (ResourceName missingPrefab, int missingCount : missingCounts)
		{
			if (missingCount > comesWith.Get(missingPrefab) && settings.IsHidden(missingPrefab))
				refused.Insert(FilePath.StripExtension(FilePath.StripPath(missingPrefab.GetPath())));
		}
		if (!refused.IsEmpty())
		{
			OYB_SendMessage(string.Format("Loadout %1 has items this server doesn't allow: %2.", slot + 1, SCR_StringHelper.Join(", ", refused)));
			return;
		}

		bool usesSupplies = arsenal.IsArsenalUsingSupplies();
		float cost;
		string needs;
		foreach (ResourceName needed, int neededCount : missingCounts)
		{
			int missing = neededCount - comesWith.Get(needed);
			needs += string.Format("%1 x%2 (%3 fitted); ", FilePath.StripPath(needed.GetPath()), neededCount, neededCount - Math.Max(missing, 0));
			if (missing <= 0)
				continue;

			if (usesSupplies)
				cost += OYB_GetPrice(needed, prices, arsenal) * missing;
		}

		PrintFormat("OYB_ARSENAL loadout %1 needs: %2", slot + 1, needs);

		map<ResourceName, float> refunds = new map<ResourceName, float>();
		if (arsenal.IsRefundEnabled())
			OYB_FindRefunds(character, arsenal, refunds);

		map<ResourceName, int> droppedCounts = new map<ResourceName, int>();
		foreach (ResourceName had, int hadCount : carried)
		{
			if (hadCount > wanted.Get(had))
				droppedCounts.Set(had, hadCount - wanted.Get(had));
		}
		map<ResourceName, int> wentWith = OYB_CountFittedParts(droppedCounts);

		// The refund for an item already covers what came fitted to it.
		float refund;
		foreach (ResourceName gone, int goneCount : droppedCounts)
		{
			int dropped = goneCount - wentWith.Get(gone);
			if (dropped > 0)
				refund += refunds.Get(gone) * dropped;
		}

		SCR_ResourceConsumer consumer = resourceComponent.GetConsumer(EResourceGeneratorID.DEFAULT, EResourceType.SUPPLIES);
		if (cost > 0)
		{
			if (!consumer)
				return;

			cost *= consumer.GetBuyMultiplier();
			if (consumer.RequestConsumtion(cost).GetReason() != EResourceReason.SUFFICIENT)
			{
				OYB_SendMessage(string.Format("Not enough supplies: this kit needs %1.", Math.Ceil(cost)));
				return;
			}
		}

		JsonLoadContext context = new JsonLoadContext();
		context.LoadFromString(saved);
		context.ReadValue(SCR_PlayerArsenalLoadout.ARSENALLOADOUT_FACTION_KEY, factionKey);
		bool applied = SCR_PlayerArsenalLoadout.ApplyLoadoutString(character, context);

		SCR_ResourceGenerator generator = resourceComponent.GetGenerator(EResourceGeneratorID.DEFAULT, EResourceType.SUPPLIES);
		if (refund > 0 && generator)
			generator.RequestGeneration(refund);

		PrintFormat("OYB_ARSENAL loadout %1 loaded for player %2 (applied %3): cost %4, refund %5", slot + 1, GetPlayerId(), applied, cost, refund);
		if (applied)
			OYB_SendMessage(string.Format("Loaded loadout %1.", slot + 1));
		else
			OYB_SendMessage("Only part of the kit could be loaded.");

		// Items spawned into the kit can take a moment to land, so the respawn kit
		// is read once they have.
		GetGame().GetCallqueue().CallLater(OYB_SetRespawnKit, 1000, false, arsenal);
	}

	// The same reach checks the game makes when an item is taken from an arsenal.
	protected SCR_ArsenalComponent OYB_FindArsenalInReach(SCR_ChimeraCharacter character, RplId arsenalResourceId)
	{
		SCR_ResourceComponent resourceComponent = SCR_ResourceComponent.Cast(Replication.FindItem(arsenalResourceId));
		if (!resourceComponent || !resourceComponent.GetOwner())
			return null;

		IEntity arsenalEntity = resourceComponent.GetOwner();
		SCR_ArsenalComponent arsenal = SCR_ArsenalComponent.FindArsenalComponent(arsenalEntity);
		InventoryStorageManagerComponent inventory = character.GetCharacterController().GetInventoryStorageManager();
		if (!arsenal || !inventory || !inventory.ValidateStorageRequest(arsenalEntity) || vector.DistanceSq(character.GetOrigin(), arsenalEntity.GetOrigin()) > OYB_MAX_ARSENAL_DISTANCE_SQ)
			return null;

		return arsenal;
	}

	// Makes what the player is wearing their respawn kit, the way the arsenal's
	// own "Save loadout" action does, unless that arsenal has saving turned off.
	// The game checks its own save rules and tells the player whether it worked.
	protected void OYB_SetRespawnKit(SCR_ArsenalComponent arsenal)
	{
		GameEntity character = GameEntity.Cast(GetControlledEntity());
		if (!character || !arsenal || arsenal.GetArsenalSaveType() == SCR_EArsenalSaveType.SAVING_DISABLED)
			return;

		SCR_ArsenalManagerComponent arsenalManager;
		if (!SCR_ArsenalManagerComponent.GetArsenalManager(arsenalManager))
			return;

		PrintFormat("OYB_ARSENAL respawn kit set for player %1", GetPlayerId());
		arsenalManager.SetPlayerArsenalLoadout(GetPlayerId(), character, arsenal, SCR_EArsenalSupplyCostType.RESPAWN_COST);
	}

	// The living character a save or load applies to. Bad slots, dead players
	// and requests closer together than a second (a held button, a spamming
	// client) get nothing, so a load can't run twice over the same kit.
	protected SCR_ChimeraCharacter OYB_GetLoadoutCharacter(int slot)
	{
		if (slot < 0 || slot >= OYB_LOADOUT_SLOTS)
			return null;

		float now = GetGame().GetWorld().GetWorldTime();
		if (now - m_fOYB_LastRequestTime < OYB_REQUEST_COOLDOWN_MS)
			return null;
		m_fOYB_LastRequestTime = now;

		SCR_ChimeraCharacter character = SCR_ChimeraCharacter.Cast(GetControlledEntity());
		if (!character || !character.GetFaction())
			return null;

		DamageManagerComponent damage = character.GetDamageManager();
		if (damage && damage.GetState() == EDamageState.DESTROYED)
			return null;

		return character;
	}

	// The kit in the game's arsenal loadout format, with its faction in front.
	protected string OYB_WriteKit(SCR_ChimeraCharacter character)
	{
		JsonSaveContext context = new JsonSaveContext();
		if (!context.WriteValue(SCR_PlayerArsenalLoadout.ARSENALLOADOUT_FACTION_KEY, character.GetFaction().GetFactionKey()))
			return string.Empty;

		if (!SCR_PlayerArsenalLoadout.ReadLoadoutString(character, context))
			return string.Empty;

		return context.SaveToString();
	}

	// Every prefab named in a saved kit, by how many times it appears. Prefab names
	// are the only strings in it that start with a {GUID}. The first one is the
	// character itself, which loading a kit never replaces, so it isn't counted.
	protected void OYB_CountPrefabs(string kit, notnull map<ResourceName, int> counts)
	{
		bool skippedCharacter;
		int pos = kit.IndexOf("\"{");
		while (pos != -1)
		{
			int start = pos + 1;
			int end = kit.IndexOfFrom(start, "\"");
			if (end == -1)
				return;

			if (end - start > 18 && kit.Get(start + 17) == "}")
			{
				string prefab = kit.Substring(start, end - start);
				prefab.Replace("\\/", "/");
				if (skippedCharacter)
					counts.Set(prefab, counts.Get(prefab) + 1);
				skippedCharacter = true;
			}
			pos = kit.IndexOfFrom(end + 1, "\"{");
		}
	}

	// Items this arsenal sells cost what it charges. Anything else in the kit, like
	// a uniform the player spawned in, is priced the way the game prices a saved
	// respawn kit: from the item catalogue, or free if it isn't listed there.
	protected float OYB_GetPrice(ResourceName prefab, map<ResourceName, float> prices, SCR_ArsenalComponent arsenal)
	{
		// A price set in the panel (or an added item's) comes first.
		int panelPrice;
		if (OYB_ArsenalSettings.Get().GetPrice(prefab, panelPrice))
			return panelPrice;

		if (prices.Contains(prefab))
			return prices.Get(prefab);

		SCR_EntityCatalogManagerComponent catalogManager = SCR_EntityCatalogManagerComponent.GetInstance();
		if (!catalogManager)
			return 0;

		SCR_EntityCatalogEntry entry = catalogManager.GetEntryWithPrefabFromAnyCatalog(EEntityCatalogType.ITEM, prefab, arsenal.GetAssignedFaction());
		if (!entry)
			return 0;

		SCR_ArsenalItem arsenalData = SCR_ArsenalItem.Cast(entry.GetEntityDataOfType(SCR_ArsenalItem));
		if (arsenalData)
			return arsenalData.GetSupplyCost(arsenal.GetSupplyCostType());

		SCR_NonArsenalItemCostCatalogData otherData = SCR_NonArsenalItemCostCatalogData.Cast(entry.GetEntityDataOfType(SCR_NonArsenalItemCostCatalogData));
		if (otherData)
			return otherData.GetSupplyCost(arsenal.GetSupplyCostType());

		return 0;
	}

	// How many of each part come already fitted to the given items, read from
	// their prefabs (and the prefabs of those parts in turn).
	protected map<ResourceName, int> OYB_CountFittedParts(map<ResourceName, int> items)
	{
		map<ResourceName, int> parts = new map<ResourceName, int>();
		foreach (ResourceName prefab, int count : items)
			OYB_AddFittedParts(prefab, count, parts, 3);

		return parts;
	}

	protected void OYB_AddFittedParts(ResourceName prefab, int times, notnull map<ResourceName, int> parts, int depth)
	{
		if (depth <= 0)
			return;

		Resource resource = Resource.Load(prefab);
		if (!resource || !resource.IsValid())
			return;

		// Each part once per item: the same setting can be reached more than one way.
		array<ResourceName> found = {};
		OYB_FindPrefabsIn(SCR_BaseContainerTools.FindEntitySource(resource), found);
		set<ResourceName> unique = new set<ResourceName>();
		foreach (ResourceName part : found)
		{
			if (part == prefab || !unique.Insert(part))
				continue;

			parts.Set(part, parts.Get(part) + times);
			OYB_AddFittedParts(part, times, parts, depth - 1);
		}
	}

	// Every prefab a prefab's settings point at: attachment and magazine slots,
	// and child entities.
	protected void OYB_FindPrefabsIn(BaseContainer container, notnull array<ResourceName> found)
	{
		if (!container)
			return;

		int vars = container.GetNumVars();
		for (int i = 0; i < vars; i++)
		{
			string varName = container.GetVarName(i);
			DataVarType type = container.GetDataVarType(i);
			if (type == DataVarType.RESOURCE_NAME)
			{
				ResourceName value;
				container.Get(varName, value);
				if (value.EndsWith(".et"))
					found.Insert(value);
			}
			else if (type == DataVarType.OBJECT)
			{
				OYB_FindPrefabsIn(container.GetObject(varName), found);
			}
			else if (type == DataVarType.OBJECT_ARRAY)
			{
				BaseContainerList list = container.GetObjectArray(varName);
				if (list)
				{
					for (int j = 0; j < list.Count(); j++)
						OYB_FindPrefabsIn(list.Get(j), found);
				}
			}
		}

		IEntitySource entitySource = IEntitySource.Cast(container);
		if (!entitySource)
			return;

		for (int k = 0; k < entitySource.GetComponentCount(); k++)
			OYB_FindPrefabsIn(entitySource.GetComponent(k), found);

		for (int c = 0; c < entitySource.GetNumChildren(); c++)
		{
			BaseContainer child = entitySource.GetChild(c);
			ResourceName childPrefab;
			while (childPrefab.IsEmpty() && child)
			{
				childPrefab = child.GetResourceName();
				child = child.GetAncestor();
			}
			if (!childPrefab.IsEmpty())
				found.Insert(childPrefab);
		}
	}

	// What the arsenal gives back for each refundable item the player has on them.
	protected void OYB_FindRefunds(IEntity entity, SCR_ArsenalComponent arsenal, notnull map<ResourceName, float> refunds)
	{
		IEntity child = entity.GetChildren();
		while (child)
		{
			InventoryItemComponent item = InventoryItemComponent.Cast(child.FindComponent(InventoryItemComponent));
			EntityPrefabData prefabData = child.GetPrefabData();
			if (item && prefabData)
			{
				SCR_ItemAttributeCollection attributes = SCR_ItemAttributeCollection.Cast(item.GetAttributes());
				ResourceName prefab = prefabData.GetPrefabName();
				if (attributes && attributes.IsRefundable() && !refunds.Contains(prefab))
				{
					float refund = Math.Max(SCR_ArsenalManagerComponent.GetItemRefundAmount(child, arsenal, false), 0);

					// The same share of a price set in the panel.
					// The same share of a price set in the panel; an added item (no
					// catalogue price) gets the arsenal's usual refund share.
					int price;
					float catalogPrice = OYB_ArsenalSettings.CatalogPrice(prefab, arsenal);
					if (OYB_ArsenalSettings.Get().GetPrice(prefab, price))
					{
						if (refund > 0 && catalogPrice > 0)
							refund = price * Math.Min(refund / catalogPrice, 1);
						else if (OYB_ArsenalSettings.Get().IsExtra(prefab))
							refund = price * OYB_RefundShare(arsenal);
					}

					refunds.Set(prefab, refund);
				}
			}

			OYB_FindRefunds(child, arsenal, refunds);
			child = child.GetSibling();
		}
	}

	protected float OYB_RefundShare(SCR_ArsenalComponent arsenal)
	{
		SCR_ResourceComponent resourceComponent = SCR_ResourceComponent.FindResourceComponent(arsenal.GetOwner());
		if (!resourceComponent)
			return 1;

		SCR_ResourceGenerator generator = resourceComponent.GetGenerator(EResourceGeneratorID.DEFAULT, EResourceType.SUPPLIES);
		if (!generator)
			return 1;

		return generator.GetResourceMultiplier();
	}

	// Named after the guns in the kit, so slots can be told apart without typing.
	protected string OYB_KitName(SCR_ChimeraCharacter character)
	{
		string name;
		foreach (IEntity weapon : OYB_TargetWeapon.GetCarried(character))
		{
			EntityPrefabData prefabData = weapon.GetPrefabData();
			if (!prefabData)
				continue;

			string weaponName = OYB_ArsenalCatalogService.OYB_ReadInventoryName(prefabData.GetPrefabName());
			if (weaponName.IsEmpty())
				continue;

			if (!name.IsEmpty())
				name += " + ";
			name += weaponName;
		}

		if (name.IsEmpty())
			return "No weapons";

		return name;
	}

	//------------------------------------------------------------------------------------------------
	// Server: the player's file

	protected string OYB_SlotFile()
	{
		UUID identity = SCR_PlayerIdentityUtils.GetPlayerIdentityId(GetPlayerId());
		string id = identity;
		if (identity.IsNull())
			id = "local_" + GetPlayerId();

		return OYB_LOADOUT_FOLDER + "/" + id + ".json";
	}

	protected void OYB_ReadSlots(notnull array<string> names, notnull array<string> loadouts)
	{
		names.Clear();
		loadouts.Clear();

		string file = OYB_SlotFile();
		if (FileIO.FileExists(file))
		{
			JsonLoadContext context = new JsonLoadContext();
			int version;
			if (!context.LoadFromFile(file) || !context.ReadValue("version", version) || !context.ReadValue("names", names) || !context.ReadValue("loadouts", loadouts))
			{
				PrintFormat("OYB_ARSENAL couldn't read %1, starting with empty slots", file, level: LogLevel.WARNING);
				names.Clear();
				loadouts.Clear();
			}
		}

		names.Resize(OYB_LOADOUT_SLOTS);
		loadouts.Resize(OYB_LOADOUT_SLOTS);
	}

	protected bool OYB_WriteSlots(array<string> names, array<string> loadouts)
	{
		FileIO.MakeDirectory(OYB_LOADOUT_FOLDER);

		JsonSaveContext context = new JsonSaveContext();
		context.WriteValue("version", 1);
		context.WriteValue("names", names);
		context.WriteValue("loadouts", loadouts);
		return context.SaveToFile(OYB_SlotFile());
	}

	//------------------------------------------------------------------------------------------------
	// Back to the player. When the player is the host, there's no one to send to.

	protected bool OYB_IsLocal()
	{
		return GetGame().GetPlayerController() == this;
	}

	protected void OYB_SendNames(array<string> names)
	{
		if (OYB_IsLocal())
			OYB_RpcDo_LoadoutNames(names);
		else
			Rpc(OYB_RpcDo_LoadoutNames, names);
	}

	protected void OYB_SendMessage(string text)
	{
		if (OYB_IsLocal())
			OYB_RpcDo_Message(text);
		else
			Rpc(OYB_RpcDo_Message, text);
	}

	[RplRpc(RplChannel.Reliable, RplRcver.Owner)]
	protected void OYB_RpcDo_LoadoutNames(array<string> names)
	{
		s_aOYB_LoadoutNames.Copy(names);
		OYB_GetOnLoadoutNames().Invoke();
	}

	[RplRpc(RplChannel.Reliable, RplRcver.Owner)]
	protected void OYB_RpcDo_Message(string text)
	{
		PrintFormat("OYB_ARSENAL %1", text);
		SCR_HintManagerComponent.ShowCustomHint(text, "Loadouts", 4);
	}
}
