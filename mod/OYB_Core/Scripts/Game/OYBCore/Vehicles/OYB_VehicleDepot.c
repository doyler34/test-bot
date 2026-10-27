// Part of the vehicle limiter (see OYB_VehicleSettings.c). This file is named to
// compile before OYB_VehicleSettings.c, which calls the methods added here.

// Getting the panel's vehicle settings to players (hidden vehicles and prices
// change what each depot shows), and telling a player when a vehicle is at its
// limit. The server sends the settings once when a player joins and again only
// when they change; limits stay on the server.
modded class SCR_PlayerController
{
	void OYB_SendVehicleSettings(notnull array<string> hidden, notnull array<string> priceItems, notnull array<int> priceCosts)
	{
		if (GetGame().GetPlayerController() == this)
			return;

		Rpc(OYB_RpcDo_VehicleSettings, hidden, priceItems, priceCosts);
	}

	[RplRpc(RplChannel.Reliable, RplRcver.Owner)]
	protected void OYB_RpcDo_VehicleSettings(array<string> hidden, array<string> priceItems, array<int> priceCosts)
	{
		OYB_VehicleSettings.Get().Apply(hidden, priceItems, priceCosts);
	}

	void OYB_SendVehicleLimitReached(string vehicleName, int limit)
	{
		if (GetGame().GetPlayerController() == this)
			OYB_RpcDo_VehicleLimitReached(vehicleName, limit);
		else
			Rpc(OYB_RpcDo_VehicleLimitReached, vehicleName, limit);
	}

	[RplRpc(RplChannel.Reliable, RplRcver.Owner)]
	protected void OYB_RpcDo_VehicleLimitReached(string vehicleName, int limit)
	{
		SCR_PopUpNotification popup = SCR_PopUpNotification.GetInstance();
		if (popup)
			popup.PopupMsg(string.Format("Vehicle limit reached (%1)", limit), 4, vehicleName);
	}
}

// The depots (the game's catalog spawner) with the panel's settings: hidden
// vehicles and variants are left out of the list, a set price is what it shows
// and charges, and a vehicle at its limit for that side isn't spawned.
modded class SCR_CatalogEntitySpawnerComponent
{
	override protected void AddAssetsFromCatalog(notnull SCR_EntityCatalog entityCatalog, bool overwriteOld = false)
	{
		super.AddAssetsFromCatalog(entityCatalog, overwriteOld);

		OYB_VehicleSettings settings = OYB_VehicleSettings.Get();
		for (int i = m_aAssetList.Count() - 1; i >= 0; i--)
		{
			SCR_EntityCatalogEntry entry = m_aAssetList[i];
			if (entry && settings.IsHidden(entry.GetPrefab()))
				m_aAssetList.Remove(i);
		}
	}

	// Has every depot rebuild its list, so hidden vehicles go or come back.
	static void OYB_RefreshAll()
	{
		foreach (SCR_CatalogEntitySpawnerComponent spawner : INSTANCES)
		{
			if (spawner)
				spawner.SetCurrentFactionCatalog();
		}
	}

	override void InitiateSpawn(notnull SCR_EntityCatalogEntry entityEntry, int userId, SCR_EntitySpawnerSlotComponent slot)
	{
		Faction faction = GetFaction();
		int limit;
		if (faction && entityEntry.GetCatalogParent() && entityEntry.GetCatalogParent().GetCatalogType() == EEntityCatalogType.VEHICLE
			&& OYB_VehicleSettings.Get().GetLimit(entityEntry.GetPrefab(), limit)
			&& OYB_VehicleLimits.CountAlive(faction.GetFactionKey(), entityEntry.GetPrefab()) >= limit)
		{
			PrintFormat("OYB_CORE vehicle limit: %1 has %2 of %3 alive, refused for player %4", faction.GetFactionKey(), limit, FilePath.StripPath(entityEntry.GetPrefab().GetPath()), userId);
			SCR_PlayerController controller = SCR_PlayerController.Cast(GetGame().GetPlayerManager().GetPlayerController(userId));
			if (controller)
				controller.OYB_SendVehicleLimitReached(WidgetManager.Translate(entityEntry.GetEntityName()), limit);
			return;
		}

		super.InitiateSpawn(entityEntry, userId, slot);
	}

	override protected void PerformSpawn(notnull SCR_EntityCatalogEntry entityEntry, IEntity user = null, SCR_EntitySpawnerSlotComponent preferredSlot = null)
	{
		super.PerformSpawn(entityEntry, user, preferredSlot);

		if (m_SpawnedEntity && m_SpawnedEntity.IsInherited(Vehicle) && GetFaction())
			OYB_VehicleLimits.Add(GetFaction().GetFactionKey(), entityEntry.GetPrefab(), m_SpawnedEntity);
	}
}

// A set price is the vehicle's cost wherever the game asks for it (the depot's
// list, its supply check and what it takes), and hidden variants aren't picked.
modded class SCR_EntityCatalogSpawnerData
{
	override int GetSupplyCost()
	{
		int price;
		if (OYB_VehicleSettings.Get().GetPrice(m_sDefaultPrefab, price))
			return price;

		return super.GetSupplyCost();
	}

	// The catalogue's own cost, for the panel's list.
	int OYB_GetOwnSupplyCost()
	{
		return m_iSupplyCost;
	}

	override ResourceName GetRandomDefaultOrVariantPrefab()
	{
		OYB_VehicleSettings settings = OYB_VehicleSettings.Get();
		array<ResourceName> prefabs = {};
		if (!settings.IsHidden(m_sDefaultPrefab))
			prefabs.Insert(m_sDefaultPrefab);

		foreach (SCR_SpawnerVariantData data : m_aVariantData)
		{
			ResourceName variant = data.GetVariantPrefabData();
			if (!SCR_StringHelper.IsEmptyOrWhiteSpace(variant) && !settings.IsHidden(variant))
				prefabs.Insert(variant);
		}

		if (prefabs.IsEmpty())
			return m_sDefaultPrefab;

		return prefabs.GetRandomElement();
	}
}

// Starts the server side once the game mode is up, and gives each joining
// player the settings.
modded class SCR_BaseGameMode
{
	override protected void OnGameStart()
	{
		super.OnGameStart();

		if (Replication.IsServer())
			GetGame().GetCallqueue().CallLater(OYB_StartVehicleSettings, 2000);
	}

	protected void OYB_StartVehicleSettings()
	{
		OYB_VehicleSettings.Get().StartServer();
	}

	override void OnPlayerConnected(int playerId)
	{
		super.OnPlayerConnected(playerId);

		if (Replication.IsServer())
			GetGame().GetCallqueue().CallLater(OYB_SendVehicleSettings, 3000, false, playerId);
	}

	protected void OYB_SendVehicleSettings(int playerId)
	{
		OYB_VehicleSettings.Get().SendTo(playerId);
	}
}
