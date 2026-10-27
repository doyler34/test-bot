// The vehicle settings, made on the OYB panel's Vehicles page: depot vehicles to
// hide (whole entries, or single variants of one), price overrides, and the
// limit groups (see OYB_VehicleLimits.c).
//
// $profile:OYB_Core/vehicles.json (written by the panel):
//   { "hidden": [prefab, ...],
//     "priceItems": [prefab, ...], "priceCosts": [supplies, ...],
//     "limitsEnabled": true (false turns every limit off),
//     "groupNames": [name, ...], "groupLimits": [-1 unlimited, 0 none, or N, ...],
//     "groupItems": [prefab, ...], "groupOf": [group name, ...] }
//
// The server writes $profile:OYB_Core/vehicle_list.json once it has started:
// every vehicle the factions' depots sell or their building menus place, mods
// included, for the panel. "prices" is the depot price (-1 when not sold at a
// depot), "buildCosts" the building menu's supply cost (-1 when not in it or
// left to the game's default), "menus" where it's offered.
class OYB_VehicleSettings
{
	protected static const string FOLDER = "$profile:OYB_Core";
	protected static const string SETTINGS_FILE = "$profile:OYB_Core/vehicles.json";
	protected static const string LIST_FILE = "$profile:OYB_Core/vehicle_list.json";
	protected static const int RELOAD_EVERY_MS = 60000;

	protected static ref OYB_VehicleSettings s_Instance;

	protected ref set<ResourceName> m_Hidden = new set<ResourceName>();
	protected ref map<ResourceName, int> m_Prices = new map<ResourceName, int>();
	protected string m_sLoadedText;
	protected bool m_bServerStarted;
	protected int m_iListed = -1;
	protected bool m_bLogged;

	protected ref array<string> m_aPrefabs = {};
	protected ref array<string> m_aNames = {};
	protected ref array<string> m_aCategories = {};
	protected ref array<string> m_aFactions = {};
	protected ref array<int> m_aPrices = {};
	protected ref array<int> m_aBuildCosts = {};
	protected ref array<string> m_aMenus = {};
	protected ref array<string> m_aVariantOf = {};
	protected ref map<string, int> m_mListed = new map<string, int>();

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

		Print("OYB_CORE vehicles: starting (list from depots and the building menu)");
		FileIO.MakeDirectory(FOLDER);
		Reload();
		GetGame().GetCallqueue().CallLater(Reload, RELOAD_EVERY_MS, true);
		WriteList();
		OYB_VehicleLimits.StartServer();
	}

	// Reads vehicles.json, and only when it changed applies it and sends it on.
	protected void Reload()
	{
		// The factions' catalogues may not be ready when the game starts; until
		// the list has vehicles in it, it's written again each time.
		if (m_iListed == 0)
			WriteList();

		string text = ReadFile(SETTINGS_FILE);
		if (text == m_sLoadedText)
			return;
		m_sLoadedText = text;

		array<string> hidden = {};
		array<string> priceItems = {};
		array<int> priceCosts = {};
		bool limitsEnabled = true;
		array<string> groupNames = {};
		array<int> groupLimits = {};
		array<string> groupItems = {};
		array<string> groupOf = {};
		if (!text.IsEmpty())
		{
			JsonLoadContext context = new JsonLoadContext();
			if (context.LoadFromString(text))
			{
				context.ReadValue("hidden", hidden);
				context.ReadValue("priceItems", priceItems);
				context.ReadValue("priceCosts", priceCosts);
				if (!context.ReadValue("limitsEnabled", limitsEnabled))
					limitsEnabled = true;
				context.ReadValue("groupNames", groupNames);
				context.ReadValue("groupLimits", groupLimits);
				context.ReadValue("groupItems", groupItems);
				context.ReadValue("groupOf", groupOf);
			}
			else
			{
				PrintFormat("OYB_CORE %1 isn't valid JSON, no vehicles hidden, priced or limited", SETTINGS_FILE, level: LogLevel.WARNING);
			}
		}

		OYB_VehicleLimits.Configure(limitsEnabled, groupNames, groupLimits, groupItems, groupOf);

		Apply(hidden, priceItems, priceCosts);
		PrintFormat("OYB_CORE vehicle settings loaded: %1 hidden, %2 priced, limits %3, %4 groups", m_Hidden.Count(), m_Prices.Count(), limitsEnabled, groupNames.Count());
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

	// Every vehicle the factions offer, mods included, and each one's variants:
	// a catalogue vehicle is on the list when a depot can sell it (it has depot
	// data) or the building menu can place it (it's in the building manager's
	// prefab list, or its editor data allows building mode). Whether a player
	// could get one right now (rank, supplies, a free slot, a limit) doesn't
	// matter here.
	protected void WriteList()
	{
		FactionManager factionManager = GetGame().GetFactionManager();
		if (!factionManager)
		{
			m_iListed = 0;
			Print("OYB_CORE vehicles: no factions yet, the list will be written later");
			return;
		}

		m_aPrefabs.Clear();
		m_aNames.Clear();
		m_aCategories.Clear();
		m_aFactions.Clear();
		m_aPrices.Clear();
		m_aBuildCosts.Clear();
		m_aMenus.Clear();
		m_aVariantOf.Clear();
		m_mListed.Clear();

		set<ResourceName> buildable = BuildingMenuPrefabs();
		if (!m_bLogged)
		{
			if (buildable)
				PrintFormat("OYB_CORE vehicles: the building manager lists %1 prefabs", buildable.Count());
			else
				Print("OYB_CORE vehicles: this game mode has no building manager");
		}

		int inCatalogues;
		array<Faction> allFactions = {};
		factionManager.GetFactionsList(allFactions);
		foreach (Faction faction : allFactions)
		{
			SCR_Faction scrFaction = SCR_Faction.Cast(faction);
			if (!scrFaction)
				continue;

			string factionKey = scrFaction.GetFactionKey();
			SCR_EntityCatalog catalog = scrFaction.GetFactionEntityCatalogOfType(EEntityCatalogType.VEHICLE, false);
			if (!catalog)
			{
				if (!m_bLogged)
					PrintFormat("OYB_CORE vehicles: %1 has no vehicle catalogue", factionKey);
				continue;
			}

			array<SCR_EntityCatalogEntry> entries = {};
			catalog.GetEntityList(entries);
			inCatalogues += entries.Count();

			int depot = 0;
			int building = 0;
			int neither = 0;
			int logged = 0;
			foreach (SCR_EntityCatalogEntry entry : entries)
			{
				ResourceName prefab = entry.GetPrefab();
				if (prefab.IsEmpty())
					continue;

				SCR_EntityCatalogSpawnerData spawnerData = SCR_EntityCatalogSpawnerData.Cast(entry.GetEntityDataOfType(SCR_EntityCatalogSpawnerData));
				SCR_EntityCatalogEditorData editorData = SCR_EntityCatalogEditorData.Cast(entry.GetEntityDataOfType(SCR_EntityCatalogEditorData));
				bool inBuildingList = buildable && buildable.Contains(prefab);
				bool buildingMode = editorData && editorData.IsValidInEditorMode(EEditorMode.BUILDING);
				bool inBuilding = inBuildingList || buildingMode;

				if (!m_bLogged && logged < 3)
				{
					logged++;
					PrintFormat("OYB_CORE vehicles: %1 %2 | data: %3 | depot data %4, in building list %5, building-mode editor data %6", factionKey, prefab, entry.OYB_DescribeData(), spawnerData != null, inBuildingList, buildingMode);
				}

				if (spawnerData)
					depot++;
				if (inBuilding)
					building++;
				if (!spawnerData && !inBuilding)
				{
					neither++;
					continue;
				}

				string menus = string.Empty;
				if (spawnerData)
					menus = "depot";
				if (inBuilding)
				{
					if (!menus.IsEmpty())
						menus += ",";
					menus += "building";
				}

				int depotPrice = -1;
				if (spawnerData)
					depotPrice = spawnerData.OYB_GetOwnSupplyCost();

				int buildCost = -1;
				if (inBuilding)
					buildCost = BuildCost(prefab);

				string category = Category(entry);
				if (!AddVehicle(prefab, WidgetManager.Translate(entry.GetEntityName()), category, factionKey, depotPrice, buildCost, menus, string.Empty))
					continue;

				if (spawnerData)
				{
					array<ResourceName> depotVariants = {};
					spawnerData.GetVariantPrefabList(depotVariants);
					foreach (ResourceName depotVariant : depotVariants)
						AddVariant(depotVariant, prefab, category, factionKey, "depot");
				}

				if (inBuilding)
				{
					array<ResourceName> buildVariants = {};
					EditableVariants(prefab, buildVariants);
					foreach (ResourceName buildVariant : buildVariants)
						AddVariant(buildVariant, prefab, category, factionKey, "building");
				}
			}

			if (!m_bLogged)
				PrintFormat("OYB_CORE vehicles: %1 has %2 vehicles in its catalogue: %3 sold at depots, %4 in the building menu, %5 in neither", factionKey, entries.Count(), depot, building, neither);
		}
		// The details are logged again on every try until a list is written.
		if (m_aPrefabs.IsEmpty())
		{
			m_iListed = 0;
			// An empty list never replaces one already written.
			if (inCatalogues == 0)
				Print("OYB_CORE vehicles: the vehicle catalogues are empty or not ready yet, vehicle_list.json left as it was; trying again in a minute");
			else
				PrintFormat("OYB_CORE vehicles: %1 catalogue vehicles but none sold at a depot or in the building menu, vehicle_list.json left as it was", inCatalogues, level: LogLevel.WARNING);
			return;
		}

		JsonSaveContext context = new JsonSaveContext();
		context.WriteValue("version", 2);
		context.WriteValue("prefabs", m_aPrefabs);
		context.WriteValue("names", m_aNames);
		context.WriteValue("categories", m_aCategories);
		context.WriteValue("factions", m_aFactions);
		context.WriteValue("prices", m_aPrices);
		context.WriteValue("buildCosts", m_aBuildCosts);
		context.WriteValue("menus", m_aMenus);
		context.WriteValue("variantOf", m_aVariantOf);
		if (!context.SaveToFile(LIST_FILE))
		{
			m_iListed = 0;
			PrintFormat("OYB_CORE couldn't write %1, trying again in a minute", LIST_FILE, level: LogLevel.WARNING);
			return;
		}

		m_iListed = m_aPrefabs.Count();
		m_bLogged = true;
		PrintFormat("OYB_CORE wrote %1 vehicles to %2", m_iListed, LIST_FILE);
	}

	// False when the prefab was already listed (another faction has it too).
	protected bool AddVehicle(ResourceName prefab, string name, string category, string factionKey, int depotPrice, int buildCost, string menus, ResourceName variantOf)
	{
		int at;
		if (m_mListed.Find(prefab, at))
		{
			m_aFactions[at] = MergeList(m_aFactions[at], factionKey);
			m_aMenus[at] = MergeList(m_aMenus[at], menus);
			if (m_aPrices[at] < 0)
				m_aPrices[at] = depotPrice;
			if (m_aBuildCosts[at] < 0)
				m_aBuildCosts[at] = buildCost;
			return false;
		}

		m_mListed.Insert(prefab, m_aPrefabs.Count());
		m_aPrefabs.Insert(prefab);
		m_aNames.Insert(name);
		m_aCategories.Insert(category);
		m_aFactions.Insert(factionKey);
		m_aPrices.Insert(depotPrice);
		m_aBuildCosts.Insert(buildCost);
		m_aMenus.Insert(menus);
		m_aVariantOf.Insert(variantOf);
		return true;
	}

	// "a,b" with each of "b,c" not already in it: "a,b,c".
	protected static string MergeList(string list, string items)
	{
		array<string> have = {};
		list.Split(",", have, true);
		array<string> adding = {};
		items.Split(",", adding, true);
		foreach (string item : adding)
		{
			if (!have.Contains(item))
				have.Insert(item);
		}

		string merged;
		foreach (int i, string piece : have)
		{
			if (i > 0)
				merged += ",";
			merged += piece;
		}
		return merged;
	}

	protected void AddVariant(ResourceName variant, ResourceName vehicle, string category, string factionKey, string menus)
	{
		if (variant.IsEmpty() || variant == vehicle)
			return;

		int at;
		if (m_mListed.Find(variant, at) && m_aVariantOf[at].IsEmpty())
			return;

		OYB_VehicleLimits.SetVariant(variant, vehicle);
		AddVehicle(variant, FilePath.StripExtension(FilePath.StripPath(variant.GetPath())), category, factionKey, -1, -1, menus, vehicle);
	}

	// What the building manager offers (the same list the building menu is
	// filled from), or null in a game mode without one.
	protected static set<ResourceName> BuildingMenuPrefabs()
	{
		BaseGameMode gameMode = GetGame().GetGameMode();
		if (!gameMode)
			return null;

		SCR_CampaignBuildingManagerComponent manager = SCR_CampaignBuildingManagerComponent.Cast(gameMode.FindComponent(SCR_CampaignBuildingManagerComponent));
		if (!manager)
			return null;

		set<ResourceName> prefabs = new set<ResourceName>();
		array<ResourceName> placeable = manager.GetPlaceablePrefabs();
		if (placeable)
		{
			foreach (ResourceName prefab : placeable)
				prefabs.Insert(prefab);
		}
		return prefabs;
	}

	// The supplies the building menu shows and charges for the vehicle (its own
	// cost and its children's), or -1 when the prefab sets none and the game
	// falls back to a cost for the entity type.
	protected static int BuildCost(ResourceName prefab)
	{
		SCR_EditableEntityUIInfo info = SCR_EditableEntityUIInfo.ExtractEditableUIInfoFromPrefab(prefab);
		if (!info)
			return -1;

		array<ref SCR_EntityBudgetValue> budgets = {};
		info.GetEntityAndChildrenBudgetCost(budgets);
		foreach (SCR_EntityBudgetValue budget : budgets)
		{
			if (budget.GetBudgetType() == EEditableEntityBudget.CAMPAIGN)
				return budget.GetBudgetValue();
		}
		return -1;
	}

	// The variants the building menu picks from at random when placing it.
	protected static void EditableVariants(ResourceName prefab, notnull array<ResourceName> variants)
	{
		Resource resource = Resource.Load(prefab);
		if (!resource || !resource.IsValid())
			return;

		IEntityComponentSource source = SCR_BaseContainerTools.FindComponentSource(resource, SCR_EditableEntityComponent);
		if (!source)
			return;

		SCR_EditableEntityVariantData variantData;
		source.Get("m_VariantData", variantData);
		if (!variantData)
			return;

		array<SCR_EditableEntityVariant> all = {};
		variantData.GetVariants(all);
		foreach (SCR_EditableEntityVariant variant : all)
			variants.Insert(variant.m_sVariantPrefab);
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
