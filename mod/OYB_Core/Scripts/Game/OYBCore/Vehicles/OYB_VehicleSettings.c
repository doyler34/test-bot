// The vehicle limiter's settings, made on the OYB panel's Vehicles page: depot
// vehicles to hide (whole entries, or single variants of one), price overrides,
// and how many of a vehicle each side may have alive at once (none set means
// unlimited).
//
// $profile:OYB_Core/vehicles.json (written by the panel):
//   { "hidden": [prefab, ...],
//     "priceItems": [prefab, ...], "priceCosts": [supplies, ...],
//     "limitItems": [prefab, ...], "limitCounts": [vehicles per side, ...] }
//
// The server writes $profile:OYB_Core/vehicle_list.json once it has started:
// every vehicle the factions' depots can offer, mods included, for the panel.
class OYB_VehicleSettings
{
	protected static const string FOLDER = "$profile:OYB_Core";
	protected static const string SETTINGS_FILE = "$profile:OYB_Core/vehicles.json";
	protected static const string LIST_FILE = "$profile:OYB_Core/vehicle_list.json";
	protected static const int RELOAD_EVERY_MS = 60000;

	protected static ref OYB_VehicleSettings s_Instance;

	protected ref set<ResourceName> m_Hidden = new set<ResourceName>();
	protected ref map<ResourceName, int> m_Prices = new map<ResourceName, int>();
	protected ref map<ResourceName, int> m_Limits = new map<ResourceName, int>();
	protected string m_sLoadedText;
	protected bool m_bServerStarted;

	static OYB_VehicleSettings Get()
	{
		if (!s_Instance)
			s_Instance = new OYB_VehicleSettings();

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

	// How many of this vehicle each side may have alive; false means unlimited.
	bool GetLimit(ResourceName prefab, out int limit)
	{
		return m_Limits.Find(prefab, limit);
	}

	void GetLists(notnull array<string> hidden, notnull array<string> priceItems, notnull array<int> priceCosts)
	{
		foreach (ResourceName hiddenPrefab : m_Hidden)
			hidden.Insert(hiddenPrefab);

		foreach (ResourceName pricedPrefab, int cost : m_Prices)
		{
			priceItems.Insert(pricedPrefab);
			priceCosts.Insert(cost);
		}
	}

	// What players need; the depots then rebuild their lists.
	void Apply(notnull array<string> hidden, notnull array<string> priceItems, notnull array<int> priceCosts)
	{
		m_Hidden.Clear();
		foreach (string prefab : hidden)
			m_Hidden.Insert(prefab);

		m_Prices.Clear();
		for (int i = 0; i < Math.Min(priceItems.Count(), priceCosts.Count()); i++)
		{
			if (priceCosts[i] >= 0)
				m_Prices.Set(priceItems[i], priceCosts[i]);
		}

		SCR_CatalogEntitySpawnerComponent.OYB_RefreshAll();
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
		WriteList();
	}

	// Reads vehicles.json, and only when it changed applies it and sends it on.
	protected void Reload()
	{
		string text = ReadFile(SETTINGS_FILE);
		if (text == m_sLoadedText)
			return;
		m_sLoadedText = text;

		array<string> hidden = {};
		array<string> priceItems = {};
		array<int> priceCosts = {};
		array<string> limitItems = {};
		array<int> limitCounts = {};
		if (!text.IsEmpty())
		{
			JsonLoadContext context = new JsonLoadContext();
			if (context.LoadFromString(text))
			{
				context.ReadValue("hidden", hidden);
				context.ReadValue("priceItems", priceItems);
				context.ReadValue("priceCosts", priceCosts);
				context.ReadValue("limitItems", limitItems);
				context.ReadValue("limitCounts", limitCounts);
			}
			else
			{
				PrintFormat("OYB_CORE %1 isn't valid JSON, no vehicles hidden, priced or limited", SETTINGS_FILE, level: LogLevel.WARNING);
			}
		}

		m_Limits.Clear();
		for (int i = 0; i < Math.Min(limitItems.Count(), limitCounts.Count()); i++)
		{
			if (!limitItems[i].IsEmpty() && limitCounts[i] >= 0)
				m_Limits.Set(limitItems[i], limitCounts[i]);
		}

		Apply(hidden, priceItems, priceCosts);
		PrintFormat("OYB_CORE vehicle settings loaded: %1 hidden, %2 priced, %3 limited", m_Hidden.Count(), m_Prices.Count(), m_Limits.Count());
		SendToAll();
	}

	void SendTo(int playerId)
	{
		SCR_PlayerController controller = SCR_PlayerController.Cast(GetGame().GetPlayerManager().GetPlayerController(playerId));
		if (!controller)
			return;

		array<string> hidden = {};
		array<string> priceItems = {};
		array<int> priceCosts = {};
		GetLists(hidden, priceItems, priceCosts);
		controller.OYB_SendVehicleSettings(hidden, priceItems, priceCosts);
	}

	protected void SendToAll()
	{
		array<int> players = {};
		GetGame().GetPlayerManager().GetPlayers(players);
		foreach (int playerId : players)
			SendTo(playerId);
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

	// Every vehicle any faction's depots can offer, and each one's variants.
	protected void WriteList()
	{
		array<string> prefabs = {};
		array<string> names = {};
		array<string> categories = {};
		array<string> factions = {};
		array<int> prices = {};
		array<string> variantOf = {};
		map<string, int> listed = new map<string, int>();

		FactionManager factionManager = GetGame().GetFactionManager();
		if (!factionManager)
			return;

		array<Faction> allFactions = {};
		factionManager.GetFactionsList(allFactions);
		foreach (Faction faction : allFactions)
		{
			SCR_Faction scrFaction = SCR_Faction.Cast(faction);
			if (!scrFaction)
				continue;

			SCR_EntityCatalog catalog = scrFaction.GetFactionEntityCatalogOfType(EEntityCatalogType.VEHICLE, false);
			if (!catalog)
				continue;

			array<SCR_EntityCatalogEntry> entries = {};
			catalog.GetEntityListWithData(SCR_EntityCatalogSpawnerData, entries);
			foreach (SCR_EntityCatalogEntry entry : entries)
			{
				ResourceName prefab = entry.GetPrefab();
				if (prefab.IsEmpty())
					continue;

				int at;
				if (listed.Find(prefab, at))
				{
					factions[at] = factions[at] + "," + scrFaction.GetFactionKey();
					continue;
				}

				SCR_EntityCatalogSpawnerData data = SCR_EntityCatalogSpawnerData.Cast(entry.GetEntityDataOfType(SCR_EntityCatalogSpawnerData));
				listed.Insert(prefab, prefabs.Count());
				prefabs.Insert(prefab);
				names.Insert(WidgetManager.Translate(entry.GetEntityName()));
				categories.Insert(Category(entry));
				factions.Insert(scrFaction.GetFactionKey());
				prices.Insert(data.OYB_GetOwnSupplyCost());
				variantOf.Insert(string.Empty);

				array<ResourceName> variants = {};
				data.GetVariantPrefabList(variants);
				foreach (ResourceName variant : variants)
				{
					if (variant.IsEmpty() || listed.Contains(variant))
						continue;

					listed.Insert(variant, prefabs.Count());
					prefabs.Insert(variant);
					names.Insert(FilePath.StripExtension(FilePath.StripPath(variant.GetPath())));
					categories.Insert(categories[categories.Count() - 1]);
					factions.Insert(scrFaction.GetFactionKey());
					prices.Insert(0);
					variantOf.Insert(prefab);
				}
			}
		}

		JsonSaveContext context = new JsonSaveContext();
		context.WriteValue("version", 1);
		context.WriteValue("prefabs", prefabs);
		context.WriteValue("names", names);
		context.WriteValue("categories", categories);
		context.WriteValue("factions", factions);
		context.WriteValue("prices", prices);
		context.WriteValue("variantOf", variantOf);
		if (context.SaveToFile(LIST_FILE))
			PrintFormat("OYB_CORE wrote %1 vehicles to %2", prefabs.Count(), LIST_FILE);
		else
			PrintFormat("OYB_CORE couldn't write %1", LIST_FILE, level: LogLevel.WARNING);
	}

	protected static string Category(SCR_EntityCatalogEntry entry)
	{
		if (entry.HasEditableEntityLabel(EEditableEntityLabel.VEHICLE_HELICOPTER))
			return "Helicopters";
		if (entry.HasEditableEntityLabel(EEditableEntityLabel.VEHICLE_AIRPLANE))
			return "Planes";
		if (entry.HasEditableEntityLabel(EEditableEntityLabel.VEHICLE_APC))
			return "Armour";
		if (entry.HasEditableEntityLabel(EEditableEntityLabel.VEHICLE_TRUCK))
			return "Trucks";
		if (entry.HasEditableEntityLabel(EEditableEntityLabel.VEHICLE_TURRET))
			return "Turrets";
		if (entry.HasEditableEntityLabel(EEditableEntityLabel.VEHICLE_CAR))
			return "Cars";
		return "Other";
	}
}

// Vehicles each side has alive from the depots, by depot entry, so a limit can
// be checked. Destroyed or deleted vehicles drop out on their own.
class OYB_VehicleLimits
{
	protected static ref map<string, ref array<IEntity>> s_mAlive = new map<string, ref array<IEntity>>();

	static void Add(string factionKey, ResourceName prefab, IEntity vehicle)
	{
		string key = factionKey + "|" + prefab;
		array<IEntity> alive = s_mAlive.Get(key);
		if (!alive)
		{
			alive = {};
			s_mAlive.Insert(key, alive);
		}
		alive.Insert(vehicle);
	}

	static int CountAlive(string factionKey, ResourceName prefab)
	{
		array<IEntity> alive = s_mAlive.Get(factionKey + "|" + prefab);
		if (!alive)
			return 0;

		for (int i = alive.Count() - 1; i >= 0; i--)
		{
			IEntity vehicle = alive[i];
			if (!vehicle || vehicle.IsDeleted())
			{
				alive.Remove(i);
				continue;
			}

			DamageManagerComponent damage = DamageManagerComponent.Cast(vehicle.FindComponent(DamageManagerComponent));
			if (damage && damage.IsDestroyed())
				alive.Remove(i);
		}
		return alive.Count();
	}
}
