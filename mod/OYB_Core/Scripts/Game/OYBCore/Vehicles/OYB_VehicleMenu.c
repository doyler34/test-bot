// Part of the vehicle limiter (see OYB_VehicleSettings.c and OYB_VehicleLimits.c).
// Named to compile after OYB_VehicleDepot.c, whose player controller methods it
// uses.

// Vehicles from the building menu, which is where Conflict hands out vehicles:
// a hidden vehicle can't be placed, a vehicle whose group is at its limit for
// the player's side is refused, and a panel price is charged on top of the
// game's own cost.
//
// A building request runs in one go on the server (the CreateEntityServer RPC):
// the game's checks (its own side's menu, cooldown, supplies) through
// CanPlaceEntityServer and IsThereEnoughBudgetToSpawn, then the spawn, then
// OnEntityCreatedServer; the supplies are only taken once the vehicle exists.
// The limit is checked in CanPlaceEntityServer after the game's checks pass, so
// a refused vehicle spawns nothing and costs nothing, and a new vehicle is
// counted in OnEntityCreatedServer before the next request is handled. A
// request that fails after the check spawns nothing, so there's nothing to
// count or give back.
//
// Only this building mode is changed; the Game Master's placing isn't.
modded class SCR_CampaignBuildingPlacingEditorComponent
{
	override protected bool CanPlaceEntityServer(IEntityComponentSource editableEntitySource, out EEditableEntityBudget blockingBudget, bool updatePreview, bool showNotification, int prefabID = -1, int playerID = -1, SCR_EditorPreviewParams params = null)
	{
		if (!super.CanPlaceEntityServer(editableEntitySource, blockingBudget, updatePreview, showNotification, prefabID, playerID, params))
			return false;

		// Only a placing request on the server has a prefab ID.
		if (prefabID < 0 || !Replication.IsServer())
			return true;

		SCR_PlacingEditorComponentClass prefabData = SCR_PlacingEditorComponentClass.Cast(GetEditorComponentData());
		if (!prefabData)
			return true;

		ResourceName prefab = prefabData.GetPrefab(prefabID);
		if (prefab.IsEmpty())
			return true;

		if (OYB_VehicleSettings.Get().IsHidden(prefab))
		{
			PrintFormat("OYB_CORE vehicles: %1 is hidden, refused for player %2", FilePath.StripPath(prefab.GetPath()), playerID);
			return false;
		}

		// The game has checked its own cost; a dearer price needs the rest too.
		int extra = OYB_PriceExtra(prefab, SCR_EditableEntityComponentClass.GetInfo(editableEntitySource));
		if (extra > 0)
		{
			SCR_ResourceConsumer consumer = OYB_SupplyConsumer();
			int cost = OYB_VehicleBudget.CampaignCost(SCR_EditableEntityComponentClass.GetInfo(editableEntitySource)) + extra;
			if (consumer && consumer.GetAggregatedResourceValue() < cost)
			{
				PrintFormat("OYB_CORE price: %1 costs %2, the base has %3, refused for player %4", FilePath.StripPath(prefab.GetPath()), cost, consumer.GetAggregatedResourceValue(), playerID);
				blockingBudget = EEditableEntityBudget.CAMPAIGN;
				return false;
			}
		}

		string factionKey = OYB_FactionKey(playerID);
		string group;
		int count, limit;
		if (OYB_VehicleLimits.Allows(factionKey, prefab, group, count, limit))
			return true;

		PrintFormat("OYB_CORE vehicle limit: %1 has %2/%3 %4, refused %5 for player %6; counted: %7", factionKey, count, limit, group, FilePath.StripPath(prefab.GetPath()), playerID, OYB_VehicleLimits.Counted(factionKey, group));
		SCR_PlayerController controller = SCR_PlayerController.Cast(GetGame().GetPlayerManager().GetPlayerController(playerID));
		if (controller)
			controller.OYB_SendVehicleLimitReached(OYB_VehicleLimits.DisplayName(group), count, limit);
		return false;
	}

	override protected void OnEntityCreatedServer(array<SCR_EditableEntityComponent> entities)
	{
		super.OnEntityCreatedServer(entities);

		if (!entities || !Replication.IsServer())
			return;

		int playerID = -1;
		if (GetManager())
			playerID = GetManager().GetPlayerID();
		string factionKey = OYB_FactionKey(playerID);

		foreach (SCR_EditableEntityComponent entity : entities)
		{
			if (!entity || !Vehicle.Cast(entity.GetOwner()))
				continue;

			OYB_VehicleLimits.Register(entity.GetOwner(), factionKey);
			OYB_ChargePrice(entity);
		}
	}

	// What a panel price adds to (or takes off) the game's own cost for the
	// vehicle itself; 0 without a price, or when the game sets no cost of its
	// own to replace.
	protected int OYB_PriceExtra(ResourceName prefab, SCR_EditableEntityUIInfo info)
	{
		int price;
		if (!OYB_VehicleSettings.Get().GetPrice(prefab, price))
			return 0;

		int own = OYB_VehicleBudget.OwnCampaignCost(info);
		if (own < 0)
			return 0;

		return price - own;
	}

	// The game has taken its own cost by now; this takes the difference to the
	// price from the same base, or gives it back when the price is lower.
	protected void OYB_ChargePrice(notnull SCR_EditableEntityComponent entity)
	{
		ResourceName prefab = SCR_ResourceNameUtils.GetPrefabName(entity.GetOwner());
		int extra = OYB_PriceExtra(prefab, SCR_EditableEntityComponentClass.GetInfo(SCR_EditableEntityComponentClass.GetEditableEntitySource(Resource.Load(prefab))));
		if (extra == 0)
			return;

		SCR_ResourceComponent resources = OYB_SupplyResources();
		if (!resources)
			return;

		if (extra > 0)
		{
			SCR_ResourceConsumer consumer = resources.GetConsumer(EResourceGeneratorID.DEFAULT, EResourceType.SUPPLIES);
			if (consumer)
				consumer.RequestConsumtion(extra);
		}
		else
		{
			SCR_ResourceGenerator generator = resources.GetGenerator(EResourceGeneratorID.DEFAULT, EResourceType.SUPPLIES);
			if (generator)
				generator.RequestGeneration(-extra);
		}
		PrintFormat("OYB_CORE price: %1 placed, %2 supplies on top of the game's cost", FilePath.StripPath(prefab.GetPath()), extra);
	}

	// The supplies of the base (or vehicle) the building menu is open at.
	protected SCR_ResourceComponent OYB_SupplyResources()
	{
		SCR_CampaignBuildingEditorComponent building = SCR_CampaignBuildingEditorComponent.Cast(FindEditorComponent(SCR_CampaignBuildingEditorComponent, false, true));
		SCR_ResourceComponent resources;
		if (!building || !building.GetProviderResourceComponent(resources) || !resources.IsResourceTypeEnabled())
			return null;
		return resources;
	}

	protected SCR_ResourceConsumer OYB_SupplyConsumer()
	{
		SCR_ResourceComponent resources = OYB_SupplyResources();
		if (!resources)
			return null;
		return resources.GetConsumer(EResourceGeneratorID.DEFAULT, EResourceType.SUPPLIES);
	}

	// The requesting player's side, or when the player has none, the side of
	// whatever provides the building menu (as the game decides which side's
	// menu to show).
	protected string OYB_FactionKey(int playerID)
	{
		if (playerID > -1)
		{
			Faction playerFaction = SCR_FactionManager.SGetPlayerFaction(playerID);
			if (playerFaction)
				return playerFaction.GetFactionKey();
		}

		if (!m_Provider)
			return string.Empty;

		FactionAffiliationComponent affiliation;
		IEntity entity = m_Provider;
		while (entity && !affiliation)
		{
			affiliation = FactionAffiliationComponent.Cast(entity.FindComponent(FactionAffiliationComponent));
			entity = entity.GetParent();
		}
		if (!affiliation)
			return string.Empty;

		Faction faction = affiliation.GetAffiliatedFaction();
		if (!faction)
			faction = affiliation.GetDefaultAffiliatedFaction();
		if (!faction)
			return string.Empty;

		return faction.GetFactionKey();
	}
}

// Hidden vehicles left out of the building menu itself, on each player's game.
// The server refuses them anyway (above); this only keeps them off the list.
modded class SCR_ContentBrowserEditorComponent
{
	// The game loads every card's info a few at a time. A card whose prefab is
	// missing (a mod listing a file that doesn't exist) stopped it with a script
	// error, so every card after it had no info and showed no price. The same
	// loading, but a prefab that can't be loaded gets an empty card, as the game
	// does for other faulty ones, and the rest still load.
	override bool EOnEditorActivateAsync(int attempt)
	{
		if (attempt == 0 || !m_aAsyncPrefabs)
			return super.EOnEditorActivateAsync(attempt);

		int tickEnd = System.GetTickCount() + ASYNC_TICK_LENGTH;
		int count = m_aAsyncPrefabs.Count();
		while (System.GetTickCount() < tickEnd)
		{
			if (m_iAsyncIndex >= count)
			{
				m_aAsyncPrefabs = null;
				FilterEntries();
				return true;
			}

			ResourceName prefab = m_aAsyncPrefabs[m_iAsyncIndex];
			SCR_EditableEntityUIInfo info = null;
			Resource resource = null;
			if (!prefab.IsEmpty())
				resource = BaseContainerTools.LoadContainer(prefab);
			if (resource && resource.IsValid())
				info = SCR_EditableEntityUIInfo.ExtractEditableUIInfoFromPrefab(prefab);
			else if (!prefab.IsEmpty())
				PrintFormat("OYB_CORE building menu: %1 can't be loaded (a mod lists a file that isn't there), left out", prefab, level: LogLevel.WARNING);

			m_aInfos.Insert(info);
			m_iAsyncIndex++;
		}
		return false;
	}

	override void FilterEntries()
	{
		super.FilterEntries();

		if (!FindEditorComponent(SCR_CampaignBuildingPlacingEditorComponent, false, true))
			return;

		OYB_VehicleSettings settings = OYB_VehicleSettings.Get();
		bool removed;
		for (int i = m_aFilteredPrefabIDs.Count() - 1; i >= 0; i--)
		{
			if (!settings.IsHidden(GetResourceNamePrefabID(m_aFilteredPrefabIDs[i])))
				continue;

			m_aFilteredPrefabIDs.RemoveOrdered(i);
			removed = true;
		}

		if (!removed)
			return;

		m_iFilteredPrefabIDsCount = m_aFilteredPrefabIDs.Count();
		Event_OnBrowserEntriesFiltered.Invoke();
	}
}
