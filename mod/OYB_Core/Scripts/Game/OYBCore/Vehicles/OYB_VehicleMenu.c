// Part of the vehicle limiter (see OYB_VehicleSettings.c and OYB_VehicleLimits.c).
// Named to compile after OYB_VehicleDepot.c, whose player controller methods it
// uses.

// Vehicles from the building menu, which is where Conflict hands out vehicles:
// a hidden vehicle can't be placed, and a vehicle whose group is at its limit
// for the player's side is refused.
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

		string factionKey = OYB_FactionKey(playerID);
		string group;
		int count, limit;
		if (OYB_VehicleLimits.Allows(factionKey, prefab, group, count, limit))
			return true;

		PrintFormat("OYB_CORE vehicle limit: %1 has %2/%3 %4, refused %5 for player %6", factionKey, count, limit, group, FilePath.StripPath(prefab.GetPath()), playerID);
		SCR_PlayerController controller = SCR_PlayerController.Cast(GetGame().GetPlayerManager().GetPlayerController(playerID));
		if (controller)
			controller.OYB_SendVehicleLimitReached(group, count, limit);
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
			if (entity && Vehicle.Cast(entity.GetOwner()))
				OYB_VehicleLimits.Register(entity.GetOwner(), factionKey);
		}
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
