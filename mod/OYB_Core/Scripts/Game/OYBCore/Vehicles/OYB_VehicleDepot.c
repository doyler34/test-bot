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

	void OYB_SendVehicleLimitReached(string group, int count, int limit)
	{
		if (GetGame().GetPlayerController() == this)
			OYB_RpcDo_VehicleLimitReached(group, count, limit);
		else
			Rpc(OYB_RpcDo_VehicleLimitReached, group, count, limit);
	}

	[RplRpc(RplChannel.Reliable, RplRcver.Owner)]
	protected void OYB_RpcDo_VehicleLimitReached(string group, int count, int limit)
	{
		SCR_PopUpNotification popup = SCR_PopUpNotification.GetInstance();
		if (popup)
			popup.PopupMsg(string.Format("%1 limit reached: %2/%3", group, count, limit), 4);
	}
}

// The depots (the game's catalog spawner) with the panel's settings: hidden
// vehicles and variants are left out of the list, a set price is what it shows
// and charges, and a vehicle whose group is at its limit for that side isn't
// spawned (see OYB_VehicleLimits.c).
//
// A depot request runs in one go on the server: the request RPC calls
// InitiateSpawn, which runs the game's own checks (supplies, side, rank,
// cooldown) and then PerformSpawn, which finds a slot, spawns the vehicle and
// only then takes the supplies. The limit is checked in InitiateSpawn before
// any of that, so a refused request spawns nothing and costs nothing, and the
// new vehicle is counted as soon as PerformSpawn returns it, before any other
// request can be handled; a spawn that fails (no free slot, a broken prefab)
// returns no vehicle and counts nothing.
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
		if (Replication.IsServer() && OYB_IsHidden(entityEntry))
		{
			PrintFormat("OYB_CORE vehicles: %1 is hidden, refused for player %2", FilePath.StripPath(entityEntry.GetPrefab().GetPath()), userId);
			return;
		}

		string factionKey;
		if (faction)
			factionKey = faction.GetFactionKey();
		string group;
		int count, limit;
		if (Replication.IsServer() && !OYB_VehicleLimits.Allows(factionKey, entityEntry.GetPrefab(), group, count, limit))
		{
			PrintFormat("OYB_CORE vehicle limit: %1 has %2/%3 %4, refused %5 for player %6", factionKey, count, limit, group, FilePath.StripPath(entityEntry.GetPrefab().GetPath()), userId);
			SCR_PlayerController controller = SCR_PlayerController.Cast(GetGame().GetPlayerManager().GetPlayerController(userId));
			if (controller)
				controller.OYB_SendVehicleLimitReached(OYB_VehicleLimits.DisplayName(group), count, limit);
			return;
		}

		super.InitiateSpawn(entityEntry, userId, slot);
	}

	// A hidden vehicle; a request sent from a list made before it was hidden is
	// refused too. A vehicle that isn't hidden can always be given out as
	// itself, even when all its variants are hidden.
	protected bool OYB_IsHidden(notnull SCR_EntityCatalogEntry entityEntry)
	{
		return OYB_VehicleSettings.Get().IsHidden(entityEntry.GetPrefab());
	}

	override protected void PerformSpawn(notnull SCR_EntityCatalogEntry entityEntry, IEntity user = null, SCR_EntitySpawnerSlotComponent preferredSlot = null)
	{
		super.PerformSpawn(entityEntry, user, preferredSlot);

		// Counted for the depot's side; the vehicle's variant prefab maps to the
		// depot vehicle's group.
		if (m_SpawnedEntity && m_SpawnedEntity.IsInherited(Vehicle) && GetFaction())
			OYB_VehicleLimits.Register(m_SpawnedEntity, GetFaction().GetFactionKey());
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
		{
			OYB_VehicleSettings.Get().ResetSession();
			GetGame().GetCallqueue().Remove(OYB_StartVehicleSettings);
			GetGame().GetCallqueue().CallLater(OYB_StartVehicleSettings, 2000);
		}
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
