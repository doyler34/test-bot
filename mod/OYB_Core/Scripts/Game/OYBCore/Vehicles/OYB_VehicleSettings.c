// The vehicle settings, made on the OYB panel's Vehicles page: vehicles to hide,
// price overrides, and the limit groups (see OYB_VehicleLimits.c).
//
// $profile:OYB_Core/vehicles.json (written by the panel):
//   { "hidden": [prefab, ...],
//     "priceItems": [prefab, ...], "priceCosts": [supplies, ...],
//     "limitsEnabled": true (false turns every limit off),
//     "groupNames": [name, ...], "groupLimits": [-1 unlimited, 0 none, or N, ...],
//     "groupItems": [prefab, ...], "groupOf": [group name, ...],
//     "categoryNames": [category, ...], "categoryLimits": [-1, 0 or N, ...] }
// A category's limit applies to every vehicle of that category in no group.
// A file that isn't valid (bad JSON, lists of different lengths, a group that
// doesn't exist) is refused and the last good settings stay in force.
//
// The server writes $profile:OYB_Core/vehicle_list.json for the panel: every
// vehicle on the server, mods included, found three ways, each row saying
// which ("sources"):
//   catalogue - the factions' vehicle catalogues (depot data, or editor data
//               for building mode);
//   building  - the building manager's list, which the building menu shows;
//   scan      - every placeable vehicle prefab in the loaded addons.
// "prices" is the depot price (-1 when not sold at a depot), "buildCosts" the
// building menu's supply cost for the vehicle itself (-1 when not in it or
// left to the game's default), "menus" where it's offered ("depot",
// "building", or empty when only found by the scan).
class OYB_VehicleSettings
{
	static const string BUILD = "vehicles-2026-10-31c";

	protected static const string FOLDER = "$profile:OYB_Core";
	protected static const string SETTINGS_FILE = "$profile:OYB_Core/vehicles.json";
	protected static const string LIST_FILE = "$profile:OYB_Core/vehicle_list.json";
	protected static const int RELOAD_EVERY_MS = 60000;
	protected static const int DISCOVER_RETRY_MS = 10000;
	protected static const int DISCOVER_TRIES = 30;
	protected static const int COUNT_RETRY_MS = 5000;
	protected static const int COUNT_TRIES = 60;
	protected static const int SCAN_BATCH = 20;
	protected static const int SCAN_BATCH_GAP_MS = 20;

	protected static ref OYB_VehicleSettings s_Instance;

	protected ref set<ResourceName> m_Hidden = new set<ResourceName>();
	protected ref map<ResourceName, int> m_Prices = new map<ResourceName, int>();
	protected ref map<string, int> m_PricesByGuid = new map<string, int>();

	// Settings file state: the text in force, and the last text refused.
	protected bool m_bHaveSettings;
	protected string m_sAcceptedText;
	protected string m_sRefusedText;
	protected bool m_bUnreadableLogged;

	protected bool m_bServerStarted;

	// Discovery and export state.
	protected int m_iDiscoverTries;
	protected int m_iCountTries;
	protected bool m_bExportPending = true;
	protected int m_iExported = -1;
	protected string m_sLastSummary;
	protected int m_iCatalogueEntries;

	protected ref array<ResourceName> m_aScanQueue;
	protected ref array<ResourceName> m_aScanned = {};
	protected bool m_bScanDone;
	protected int m_iScanStarted;
	protected int m_iScanChecked;

	protected ref array<string> m_aPrefabs = {};
	protected ref array<string> m_aNames = {};
	protected ref array<string> m_aCategories = {};
	protected ref array<string> m_aFactions = {};
	protected ref array<int> m_aPrices = {};
	protected ref array<int> m_aBuildCosts = {};
	protected ref array<string> m_aMenus = {};
	protected ref array<string> m_aSources = {};
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

	// A vehicle's price override; a variant costs what its vehicle costs. Also
	// matched by GUID, as a prefab read from a component source may come
	// without the same path spelling.
	bool GetPrice(ResourceName prefab, out int price)
	{
		if (prefab.IsEmpty())
			return false;

		if (m_Prices.Find(prefab, price) || m_PricesByGuid.Find(SCR_ResourceNameUtils.GetPrefabGUID(prefab), price))
			return true;

		ResourceName vehicle = OYB_VehicleLimits.VariantOf(prefab);
		if (vehicle.IsEmpty())
			return false;

		return m_Prices.Find(vehicle, price) || m_PricesByGuid.Find(SCR_ResourceNameUtils.GetPrefabGUID(vehicle), price);
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
		m_PricesByGuid.Clear();
		for (int i = 0; i < Math.Min(priceItems.Count(), priceCosts.Count()); i++)
		{
			if (priceCosts[i] < 0 || priceItems[i].IsEmpty())
				continue;

			m_Prices.Set(priceItems[i], priceCosts[i]);
			m_PricesByGuid.Set(SCR_ResourceNameUtils.GetPrefabGUID(priceItems[i]), priceCosts[i]);
		}

		SCR_CatalogEntitySpawnerComponent.OYB_RefreshAll();
	}

	//------------------------------------------------------------------------------------------------
	// Server

	// A new game (a scenario restart keeps script state): stops what the last
	// one started, so nothing runs or counts twice.
	void ResetSession()
	{
		ScriptCallQueue queue = GetGame().GetCallqueue();
		queue.Remove(Reload);
		queue.Remove(Discover);
		queue.Remove(ScanBatch);
		queue.Remove(StartCounting);

		m_bServerStarted = false;
		m_iDiscoverTries = 0;
		m_iCountTries = 0;
		m_bExportPending = true;
		m_iExported = -1;
		m_sLastSummary = string.Empty;
		m_aScanQueue = null;
		m_aScanned.Clear();
		m_bScanDone = false;

		OYB_VehicleLimits.ResetSession();
	}

	void StartServer()
	{
		if (m_bServerStarted || !Replication.IsServer())
			return;
		m_bServerStarted = true;

		PrintFormat("OYB_CORE vehicles: build %1 starting; settings %2, list %3", BUILD, SETTINGS_FILE, LIST_FILE);
		FileIO.MakeDirectory(FOLDER);
		Reload();
		GetGame().GetCallqueue().CallLater(Reload, RELOAD_EVERY_MS, true);
		Discover();
		StartScan();
		StartCounting();
	}

	// Counts the vehicles already in the world once the editor core is up and
	// a saved game has finished loading.
	protected void StartCounting()
	{
		if (OYB_VehicleLimits.StartServer())
			return;

		if (m_iCountTries >= COUNT_TRIES)
		{
			PrintFormat("OYB_CORE vehicle limits: the world wasn't ready after %1 s, vehicles already in it aren't counted", COUNT_TRIES * COUNT_RETRY_MS / 1000, level: LogLevel.WARNING);
			return;
		}

		m_iCountTries++;
		GetGame().GetCallqueue().CallLater(StartCounting, COUNT_RETRY_MS);
	}

	// Reads vehicles.json, and when it changed and is valid applies it and sends
	// it on. Also writes the vehicle list again while it's still owed.
	protected void Reload()
	{
		if (m_bExportPending)
			Discover();

		bool exists;
		string text;
		if (!ReadFile(SETTINGS_FILE, exists, text))
		{
			if (!m_bUnreadableLogged)
				PrintFormat("OYB_CORE %1 couldn't be read, keeping the settings in force", SETTINGS_FILE, level: LogLevel.WARNING);
			m_bUnreadableLogged = true;
			return;
		}
		m_bUnreadableLogged = false;

		if (m_bHaveSettings && text == m_sAcceptedText)
			return;
		if (text == m_sRefusedText && !text.IsEmpty())
			return;

		array<string> hidden = {};
		array<string> priceItems = {};
		array<int> priceCosts = {};
		bool limitsEnabled = true;
		array<string> groupNames = {};
		array<int> groupLimits = {};
		array<string> groupItems = {};
		array<string> groupOf = {};
		array<string> categoryNames = {};
		array<int> categoryLimits = {};

		// No file at all is a server without settings yet; a file being written
		// can be empty for a moment, which isn't.
		if (exists)
		{
			string error = Parse(text, hidden, priceItems, priceCosts, limitsEnabled, groupNames, groupLimits, groupItems, groupOf, categoryNames, categoryLimits);
			if (!error.IsEmpty())
			{
				m_sRefusedText = text;
				PrintFormat("OYB_CORE %1 refused (%2), keeping the settings in force", SETTINGS_FILE, error, level: LogLevel.WARNING);
				return;
			}
		}

		m_bHaveSettings = true;
		m_sAcceptedText = text;
		m_sRefusedText = string.Empty;

		OYB_VehicleLimits.Configure(limitsEnabled, groupNames, groupLimits, groupItems, groupOf, categoryNames, categoryLimits);
		Apply(hidden, priceItems, priceCosts);
		PrintFormat("OYB_CORE vehicle settings loaded: %1 hidden, %2 priced, limits %3, %4 groups, %5 category limits", m_Hidden.Count(), m_Prices.Count(), limitsEnabled, groupNames.Count(), categoryNames.Count());
		SendToAll();
	}

	// Empty when the settings are usable, else what's wrong with them.
	protected static string Parse(string text, notnull array<string> hidden, notnull array<string> priceItems, notnull array<int> priceCosts, out bool limitsEnabled,
		notnull array<string> groupNames, notnull array<int> groupLimits, notnull array<string> groupItems, notnull array<string> groupOf,
		notnull array<string> categoryNames, notnull array<int> categoryLimits)
	{
		string trimmed = text.Trim();
		if (trimmed.IsEmpty())
			return "empty file";

		JsonLoadContext context = new JsonLoadContext();
		if (!context.LoadFromString(text))
			return "not valid JSON";

		context.ReadValue("hidden", hidden);
		context.ReadValue("priceItems", priceItems);
		context.ReadValue("priceCosts", priceCosts);
		if (!context.ReadValue("limitsEnabled", limitsEnabled))
			limitsEnabled = true;
		context.ReadValue("groupNames", groupNames);
		context.ReadValue("groupLimits", groupLimits);
		context.ReadValue("groupItems", groupItems);
		context.ReadValue("groupOf", groupOf);
		context.ReadValue("categoryNames", categoryNames);
		context.ReadValue("categoryLimits", categoryLimits);

		if (categoryNames.Count() != categoryLimits.Count())
			return "categoryNames and categoryLimits differ in length";
		foreach (int categoryLimit : categoryLimits)
		{
			if (categoryLimit < OYB_VehicleLimits.UNLIMITED)
				return "a category limit below -1";
		}

		if (priceItems.Count() != priceCosts.Count())
			return "priceItems and priceCosts differ in length";
		if (groupNames.Count() != groupLimits.Count())
			return "groupNames and groupLimits differ in length";
		if (groupItems.Count() != groupOf.Count())
			return "groupItems and groupOf differ in length";

		foreach (int cost : priceCosts)
		{
			if (cost < 0)
				return "a negative price";
		}

		foreach (int i, string name : groupNames)
		{
			if (name.IsEmpty())
				return "a group without a name";
			if (groupLimits[i] < OYB_VehicleLimits.UNLIMITED)
				return "a group limit below -1";
		}

		foreach (string group : groupOf)
		{
			if (!groupNames.Contains(group))
				return "a vehicle in a group that doesn't exist: " + group;
		}
		return string.Empty;
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

	//------------------------------------------------------------------------------------------------
	// The vehicle list

	// Builds the list from the catalogues, the building manager's list and the
	// scan's results so far, and writes it when it has vehicles in it. Whether
	// a player could get one right now (rank, supplies, a free slot, a limit)
	// doesn't matter here, and hidden vehicles stay on it.
	protected void Discover()
	{
		FactionManager factionManager = GetGame().GetFactionManager();
		if (!factionManager)
		{
			LogOnChange("OYB_CORE vehicles: no factions yet, the list will be written later");
			RetryDiscover();
			return;
		}

		ClearRows();

		array<SCR_Faction> factions = {};
		array<Faction> allFactions = {};
		factionManager.GetFactionsList(allFactions);
		foreach (Faction faction : allFactions)
		{
			SCR_Faction scrFaction = SCR_Faction.Cast(faction);
			if (scrFaction)
				factions.Insert(scrFaction);
		}

		set<ResourceName> buildable = BuildingMenuPrefabs();
		string details;
		m_iCatalogueEntries = AddCatalogueVehicles(factions, buildable, details);
		int building = AddBuildingVehicles(buildable, factions);
		int scanned = AddScannedVehicles(factions);

		int buildableCount = -1;
		if (buildable)
			buildableCount = buildable.Count();

		string summary = string.Format("OYB_CORE vehicles: build %1; %2 catalogue entries; building manager list %3 prefabs (-1: none), %4 of them vehicles not in a catalogue; scan %5 (%6 vehicles); %7 rows",
			BUILD, m_iCatalogueEntries, buildableCount, building, ScanState(), scanned, m_aPrefabs.Count());
		bool changed = LogOnChange(summary);
		if (changed)
			PrintFormat("%1", details);

		if (m_aPrefabs.IsEmpty())
		{
			m_bExportPending = true;
			if (changed)
				Print("OYB_CORE vehicles: nothing found yet; vehicle_list.json left as it was", LogLevel.WARNING);
			RetryDiscover();
			return;
		}

		Export();

		// The catalogues may fill a little after the game starts.
		if (m_iCatalogueEntries == 0)
			RetryDiscover();
	}

	protected void RetryDiscover()
	{
		if (m_iDiscoverTries >= DISCOVER_TRIES)
			return;

		m_iDiscoverTries++;
		GetGame().GetCallqueue().Remove(Discover);
		GetGame().GetCallqueue().CallLater(Discover, DISCOVER_RETRY_MS);
	}

	// True (and printed) when the text differs from the last one printed.
	protected bool LogOnChange(string text)
	{
		if (text == m_sLastSummary)
			return false;

		m_sLastSummary = text;
		PrintFormat("%1", text);
		return true;
	}

	protected void Export()
	{
		JsonSaveContext context = new JsonSaveContext();
		context.WriteValue("version", 3);
		context.WriteValue("build", BUILD);
		context.WriteValue("scanComplete", m_bScanDone);
		context.WriteValue("catalogueEntries", m_iCatalogueEntries);
		context.WriteValue("prefabs", m_aPrefabs);
		context.WriteValue("names", m_aNames);
		context.WriteValue("categories", m_aCategories);
		context.WriteValue("factions", m_aFactions);
		context.WriteValue("prices", m_aPrices);
		context.WriteValue("buildCosts", m_aBuildCosts);
		context.WriteValue("menus", m_aMenus);
		context.WriteValue("sources", m_aSources);
		context.WriteValue("variantOf", m_aVariantOf);
		if (!context.SaveToFile(LIST_FILE))
		{
			m_bExportPending = true;
			PrintFormat("OYB_CORE couldn't write %1, trying again in a minute", LIST_FILE, level: LogLevel.WARNING);
			return;
		}

		m_bExportPending = false;
		if (m_iExported != m_aPrefabs.Count())
			PrintFormat("OYB_CORE wrote %1 vehicles to %2 (build %3)", m_aPrefabs.Count(), LIST_FILE, BUILD);
		m_iExported = m_aPrefabs.Count();
	}

	protected void ClearRows()
	{
		m_aPrefabs.Clear();
		m_aNames.Clear();
		m_aCategories.Clear();
		m_aFactions.Clear();
		m_aPrices.Clear();
		m_aBuildCosts.Clear();
		m_aMenus.Clear();
		m_aSources.Clear();
		m_aVariantOf.Clear();
		m_mListed.Clear();
	}

	// Catalogue vehicles a depot sells (depot data) or the building menu places
	// (in the building manager's list, or editor data for building mode).
	// Returns how many entries the catalogues hold; details gets a line per
	// faction and the data of a few entries.
	protected int AddCatalogueVehicles(notnull array<SCR_Faction> factions, set<ResourceName> buildable, out string details)
	{
		int inCatalogues;
		foreach (SCR_Faction faction : factions)
		{
			string factionKey = faction.GetFactionKey();
			SCR_EntityCatalog catalog = faction.GetFactionEntityCatalogOfType(EEntityCatalogType.VEHICLE, false);
			if (!catalog)
			{
				details += string.Format("OYB_CORE vehicles: %1 has no vehicle catalogue\n", factionKey);
				continue;
			}

			array<SCR_EntityCatalogEntry> entries = {};
			catalog.GetEntityList(entries);
			inCatalogues += entries.Count();

			int depot = 0;
			int building = 0;
			int neither = 0;
			int described = 0;
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

				if (described < 3)
				{
					described++;
					details += string.Format("OYB_CORE vehicles: %1 %2 | data: %3 | depot data %4, in building list %5, building-mode editor data %6\n", factionKey, prefab, DescribeData(entry), spawnerData != null, inBuildingList, buildingMode);
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
					menus = MergeList(menus, "building");

				int depotPrice = -1;
				if (spawnerData)
					depotPrice = spawnerData.OYB_GetOwnSupplyCost();

				int buildCost = -1;
				if (inBuilding)
					buildCost = BuildCost(prefab);

				string category = Category(entry);
				AddRow(prefab, WidgetManager.Translate(entry.GetEntityName()), category, factionKey, depotPrice, buildCost, menus, "catalogue", string.Empty);

				if (spawnerData)
				{
					array<ResourceName> depotVariants = {};
					spawnerData.GetVariantPrefabList(depotVariants);
					foreach (ResourceName depotVariant : depotVariants)
						AddVariant(depotVariant, prefab, category, factionKey, "depot", "catalogue");
				}

				if (inBuilding)
				{
					array<ResourceName> buildVariants = {};
					EditableVariants(prefab, buildVariants);
					foreach (ResourceName buildVariant : buildVariants)
						AddVariant(buildVariant, prefab, category, factionKey, "building", "catalogue");
				}
			}

			details += string.Format("OYB_CORE vehicles: %1 has %2 vehicles in its catalogue: %3 sold at depots, %4 in the building menu, %5 in neither\n", factionKey, entries.Count(), depot, building, neither);
		}
		return inCatalogues;
	}

	// Vehicles in the building manager's list (the building menu's list). The
	// list also holds structures and compositions, which are left out: only
	// prefabs that are vehicles themselves count.
	protected int AddBuildingVehicles(set<ResourceName> buildable, notnull array<SCR_Faction> factions)
	{
		if (!buildable)
			return 0;

		int added;
		foreach (ResourceName prefab : buildable)
		{
			if (prefab.IsEmpty())
				continue;

			SCR_EditableEntityUIInfo info = VehicleInfo(prefab, false);
			if (!info)
				continue;

			if (!m_mListed.Contains(prefab))
				added++;

			string factionKeys = FactionsOf(info, factions);
			string category = CategoryOf(info);
			AddRow(prefab, WidgetManager.Translate(info.GetName()), category, factionKeys, -1, BuildCost(prefab), "building", "building", string.Empty);

			array<ResourceName> variants = {};
			EditableVariants(prefab, variants);
			foreach (ResourceName variant : variants)
				AddVariant(variant, prefab, category, factionKeys, "building", "building");
		}
		return added;
	}

	// Vehicles the scan found: on the server but not in any menu OYB knows.
	protected int AddScannedVehicles(notnull array<SCR_Faction> factions)
	{
		int added;
		foreach (ResourceName prefab : m_aScanned)
		{
			int at;
			if (m_mListed.Find(prefab, at))
			{
				m_aSources[at] = MergeList(m_aSources[at], "scan");
				continue;
			}

			SCR_EditableEntityUIInfo info = VehicleInfo(prefab, true);
			if (!info)
				continue;

			AddRow(prefab, WidgetManager.Translate(info.GetName()), CategoryOf(info), FactionsOf(info, factions), -1, -1, string.Empty, "scan", string.Empty);
			added++;
		}
		return added;
	}

	// One row per exact prefab; a prefab found again (another faction, another
	// source) adds what's new to its row.
	protected void AddRow(ResourceName prefab, string name, string category, string factionKeys, int depotPrice, int buildCost, string menus, string sources, ResourceName variantOf)
	{
		int at;
		if (m_mListed.Find(prefab, at))
		{
			m_aFactions[at] = MergeList(m_aFactions[at], factionKeys);
			m_aMenus[at] = MergeList(m_aMenus[at], menus);
			m_aSources[at] = MergeList(m_aSources[at], sources);
			if (m_aPrices[at] < 0)
				m_aPrices[at] = depotPrice;
			if (m_aBuildCosts[at] < 0)
				m_aBuildCosts[at] = buildCost;
			if (m_aNames[at].IsEmpty())
				m_aNames[at] = name;
			return;
		}

		OYB_VehicleLimits.SetCategory(prefab, category);
		m_mListed.Insert(prefab, m_aPrefabs.Count());
		m_aPrefabs.Insert(prefab);
		m_aNames.Insert(name);
		m_aCategories.Insert(category);
		m_aFactions.Insert(factionKeys);
		m_aPrices.Insert(depotPrice);
		m_aBuildCosts.Insert(buildCost);
		m_aMenus.Insert(menus);
		m_aSources.Insert(sources);
		m_aVariantOf.Insert(variantOf);
	}

	// A variant row, or when the variant is also a vehicle of its own, just the
	// new factions and menus on that row.
	protected void AddVariant(ResourceName variant, ResourceName vehicle, string category, string factionKeys, string menus, string sources)
	{
		if (variant.IsEmpty() || variant == vehicle)
			return;

		int at;
		if (!m_mListed.Find(variant, at) || !m_aVariantOf[at].IsEmpty())
			OYB_VehicleLimits.SetVariant(variant, vehicle);

		AddRow(variant, FilePath.StripExtension(FilePath.StripPath(variant.GetPath())), category, factionKeys, -1, -1, menus, sources, vehicle);
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

	// The prefab's editable info when it's a vehicle: a Vehicle entity whose
	// editable entity is of the vehicle type and, when asked, one the editor
	// can place (which leaves out the base prefabs vehicles inherit from).
	protected static SCR_EditableEntityUIInfo VehicleInfo(ResourceName prefab, bool placeableOnly)
	{
		Resource resource = Resource.Load(prefab);
		if (!resource || !resource.IsValid())
			return null;

		IEntitySource entitySource = resource.GetResource().ToEntitySource();
		if (!entitySource)
			return null;

		typename entityType = entitySource.GetClassName().ToType();
		if (!entityType || !entityType.IsInherited(Vehicle))
			return null;

		IEntityComponentSource editableSource = SCR_EditableEntityComponentClass.GetEditableEntitySource(entitySource);
		if (!editableSource || SCR_EditableEntityComponentClass.GetEntityType(editableSource) != EEditableEntityType.VEHICLE)
			return null;

		if (placeableOnly && !(SCR_EditableEntityComponentClass.GetEntityFlags(editableSource) & EEditableEntityFlag.PLACEABLE))
			return null;

		return SCR_EditableEntityComponentClass.GetInfo(editableSource);
	}

	// The factions a vehicle belongs to: its own faction, and those whose label
	// it carries (as the building menu matches them).
	protected static string FactionsOf(notnull SCR_EditableEntityUIInfo info, notnull array<SCR_Faction> factions)
	{
		string keys = info.GetFactionKey();
		array<EEditableEntityLabel> labels = {};
		info.GetEntityLabels(labels);
		foreach (SCR_Faction faction : factions)
		{
			if (labels.Contains(faction.GetFactionLabel()))
				keys = MergeList(keys, faction.GetFactionKey());
		}
		return keys;
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

	// The vehicle's own supply cost in the building menu (what a price
	// override replaces; crew costs come on top as the game sets them), or -1
	// when the prefab sets none and the game falls back to a cost for the
	// entity type.
	protected static int BuildCost(ResourceName prefab)
	{
		SCR_EditableEntityUIInfo info = SCR_EditableEntityUIInfo.ExtractEditableUIInfoFromPrefab(prefab);
		if (!info)
			return -1;

		return OYB_VehicleBudget.OwnCampaignCost(info);
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

	// The entry's enabled data blocks, for the log.
	protected static string DescribeData(notnull SCR_EntityCatalogEntry entry)
	{
		array<SCR_BaseEntityCatalogData> dataList = {};
		entry.GetEntityDataList(dataList);
		if (dataList.IsEmpty())
			return "none enabled";

		string text;
		foreach (SCR_BaseEntityCatalogData data : dataList)
		{
			if (!data)
				continue;

			if (!text.IsEmpty())
				text += ", ";
			text += data.Type().ToString();

			SCR_EntityCatalogEditorData editorData = SCR_EntityCatalogEditorData.Cast(data);
			if (editorData)
				text += string.Format(" modes %1", editorData.GetValidEditorModes());
		}
		return text;
	}

	protected static string Category(SCR_EntityCatalogEntry entry)
	{
		array<EEditableEntityLabel> labels = {};
		entry.GetEditableEntityLabels(labels);
		return CategoryOfLabels(labels);
	}

	protected static string CategoryOf(notnull SCR_EditableEntityUIInfo info)
	{
		array<EEditableEntityLabel> labels = {};
		info.GetEntityLabels(labels);
		return CategoryOfLabels(labels);
	}

	protected static string CategoryOfLabels(notnull array<EEditableEntityLabel> labels)
	{
		if (labels.Contains(EEditableEntityLabel.VEHICLE_HELICOPTER))
			return "Helicopters";
		if (labels.Contains(EEditableEntityLabel.VEHICLE_AIRPLANE))
			return "Planes";
		if (labels.Contains(EEditableEntityLabel.VEHICLE_APC))
			return "Armour";
		if (labels.Contains(EEditableEntityLabel.VEHICLE_TRUCK))
			return "Trucks";
		if (labels.Contains(EEditableEntityLabel.VEHICLE_TURRET))
			return "Turrets";
		if (labels.Contains(EEditableEntityLabel.VEHICLE_CAR))
			return "Cars";
		return "Other";
	}

	//------------------------------------------------------------------------------------------------
	// The scan: every prefab in the loaded addons' vehicle folders (the base
	// game's Prefabs/Vehicles, each mod's whole Prefabs folder), checked a few
	// at a time so a big modpack doesn't hold the server up.

	protected string ScanState()
	{
		if (m_bScanDone)
			return string.Format("done, %1 prefabs checked", m_iScanChecked);
		if (m_aScanQueue)
			return string.Format("running, %1 left", m_aScanQueue.Count());
		return "not started";
	}

	protected void StartScan()
	{
		m_aScanQueue = {};
		m_aScanned.Clear();
		m_iScanChecked = 0;
		m_iScanStarted = System.GetTickCount();

		array<string> addons = {};
		GameProject.GetLoadedAddons(addons);
		foreach (string guid : addons)
		{
			string addonId = GameProject.GetAddonID(guid);
			if (addonId.IsEmpty())
				continue;

			SearchResourcesFilter filter = new SearchResourcesFilter();
			if (GameProject.IsVanillaAddon(guid))
				filter.rootPath = "$" + addonId + ":Prefabs/Vehicles";
			else
				filter.rootPath = "$" + addonId + ":Prefabs";
			filter.fileExtensions = {"et"};
			ResourceDatabase.SearchResources(filter, OnScanFound);
		}

		PrintFormat("OYB_CORE vehicles: scanning %1 prefabs in %2 addons for vehicles", m_aScanQueue.Count(), addons.Count());
		GetGame().GetCallqueue().CallLater(ScanBatch, SCAN_BATCH_GAP_MS);
	}

	protected void OnScanFound(ResourceName resourceName, string exactPath = "")
	{
		if (m_aScanQueue)
			m_aScanQueue.Insert(resourceName);
	}

	protected void ScanBatch()
	{
		if (!m_aScanQueue)
			return;

		int done;
		while (!m_aScanQueue.IsEmpty() && done < SCAN_BATCH)
		{
			ResourceName prefab = m_aScanQueue[m_aScanQueue.Count() - 1];
			m_aScanQueue.Remove(m_aScanQueue.Count() - 1);
			done++;
			m_iScanChecked++;

			if (!m_aScanned.Contains(prefab) && VehicleInfo(prefab, true))
				m_aScanned.Insert(prefab);
		}

		if (!m_aScanQueue.IsEmpty())
		{
			GetGame().GetCallqueue().CallLater(ScanBatch, SCAN_BATCH_GAP_MS);
			return;
		}

		m_aScanQueue = null;
		m_bScanDone = true;
		PrintFormat("OYB_CORE vehicles: scan found %1 placeable vehicles in %2 prefabs (%3 ms)", m_aScanned.Count(), m_iScanChecked, System.GetTickCount(m_iScanStarted));
		Discover();
	}
}
