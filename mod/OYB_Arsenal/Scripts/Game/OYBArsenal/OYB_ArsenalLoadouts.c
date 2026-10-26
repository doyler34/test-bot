// Saved loadouts: five slots per player, kept by the server in its profile folder
// so a client can't hand it a made-up kit. Saving writes the player's kit with the
// game's own loadout format (the one used for respawning with an arsenal kit).
// Loading at an arsenal only pays for what the player doesn't already carry, and
// each of those items has to be offered by that arsenal. Whatever the kit throws
// away is refunded the way the arsenal refunds it when an item is put back.
modded class SCR_PlayerController
{
	static const int OYB_LOADOUT_SLOTS = 5;
	protected static const string OYB_LOADOUT_FOLDER = "$profile:OYB_Arsenal";
	protected static const float OYB_MAX_ARSENAL_DISTANCE_SQ = 900;

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

	void OYB_RequestLoadoutNames()
	{
		Rpc(OYB_RpcAsk_LoadoutNames);
	}

	void OYB_SaveLoadout(int slot)
	{
		Rpc(OYB_RpcAsk_SaveLoadout, slot);
	}

	void OYB_LoadLoadout(int slot, RplId arsenalResourceId)
	{
		Rpc(OYB_RpcAsk_LoadLoadout, slot, arsenalResourceId);
	}

	//------------------------------------------------------------------------------------------------
	// Server

	[RplRpc(RplChannel.Reliable, RplRcver.Server)]
	protected void OYB_RpcAsk_LoadoutNames()
	{
		array<string> names = {};
		array<string> loadouts = {};
		OYB_ReadSlots(names, loadouts);
		OYB_SendNames(names);
	}

	[RplRpc(RplChannel.Reliable, RplRcver.Server)]
	protected void OYB_RpcAsk_SaveLoadout(int slot)
	{
		if (slot < 0 || slot >= OYB_LOADOUT_SLOTS)
			return;

		SCR_ChimeraCharacter character = SCR_ChimeraCharacter.Cast(GetControlledEntity());
		if (!character || !character.GetFaction())
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
		OYB_SendMessage(string.Format("Saved to slot %1: %2", slot + 1, names[slot]));
	}

	[RplRpc(RplChannel.Reliable, RplRcver.Server)]
	protected void OYB_RpcAsk_LoadLoadout(int slot, RplId arsenalResourceId)
	{
		if (slot < 0 || slot >= OYB_LOADOUT_SLOTS)
			return;

		SCR_ChimeraCharacter character = SCR_ChimeraCharacter.Cast(GetControlledEntity());
		if (!character || !character.GetFaction())
			return;

		SCR_ResourceComponent resourceComponent = SCR_ResourceComponent.Cast(Replication.FindItem(arsenalResourceId));
		if (!resourceComponent || !resourceComponent.GetOwner())
			return;

		// The same reach checks the game makes when an item is taken from an arsenal.
		IEntity arsenalEntity = resourceComponent.GetOwner();
		SCR_ArsenalComponent arsenal = SCR_ArsenalComponent.FindArsenalComponent(arsenalEntity);
		InventoryStorageManagerComponent inventory = character.GetCharacterController().GetInventoryStorageManager();
		if (!arsenal || !inventory || !inventory.ValidateStorageRequest(arsenalEntity) || vector.DistanceSq(character.GetOrigin(), arsenalEntity.GetOrigin()) > OYB_MAX_ARSENAL_DISTANCE_SQ)
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

		map<ResourceName, float> prices = new map<ResourceName, float>();
		array<SCR_ArsenalItem> offered = {};
		arsenal.GetFilteredArsenalItems(offered);
		foreach (SCR_ArsenalItem item : offered)
		{
			if (item)
				prices.Set(item.GetItemResourceName(), item.GetSupplyCost(arsenal.GetSupplyCostType()));
		}

		bool usesSupplies = arsenal.IsArsenalUsingSupplies();
		float cost;
		foreach (ResourceName prefab, int count : wanted)
		{
			int missing = count - carried.Get(prefab);
			if (missing <= 0)
				continue;

			if (!prices.Contains(prefab))
			{
				string itemName = OYB_ArsenalCatalogService.OYB_ReadInventoryName(prefab);
				if (itemName.IsEmpty())
					itemName = FilePath.StripExtension(FilePath.StripPath(prefab.GetPath()));
				OYB_SendMessage(string.Format("This arsenal doesn't have %1.", itemName));
				return;
			}

			if (usesSupplies)
				cost += prices.Get(prefab) * missing;
		}

		map<ResourceName, float> refunds = new map<ResourceName, float>();
		if (arsenal.IsRefundEnabled())
			OYB_FindRefunds(character, arsenal, refunds);

		float refund;
		foreach (ResourceName had, int hadCount : carried)
		{
			int dropped = hadCount - wanted.Get(had);
			if (dropped > 0)
				refund += refunds.Get(had) * dropped;
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
			OYB_SendMessage(string.Format("Loaded %1.", names[slot]));
		else
			OYB_SendMessage("Only part of the kit could be loaded.");
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
	// are the only strings in it that start with a {GUID}.
	protected void OYB_CountPrefabs(string kit, notnull map<ResourceName, int> counts)
	{
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
				counts.Set(prefab, counts.Get(prefab) + 1);
			}
			pos = kit.IndexOfFrom(end + 1, "\"{");
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
					refunds.Set(prefab, Math.Max(SCR_ArsenalManagerComponent.GetItemRefundAmount(child, arsenal, false), 0));
			}

			OYB_FindRefunds(child, arsenal, refunds);
			child = child.GetSibling();
		}
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
