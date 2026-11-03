// Part of the vehicle limiter (see OYB_VehicleSettings.c).

// Building menu costs set on the panel's Vehicles page. The panel writes
// $profile:OYB_Core/vehicle_costs.json:
//   { "prefabs": [prefab, ...], "costs": [supplies, ...] }
// The server reads it once, when a game starts, and puts each cost into the
// vehicle's own data (the campaign entry of its editable entity's budget list),
// where the card, the supply check and the charge all read it, the same as a
// cost the vehicle came with. Each player is sent the same list when they join
// and does the same, so the cards match. A change on the panel takes effect at
// the next game; a vehicle taken off the list gets its own cost back then.
class OYB_VehicleCosts
{
	protected static const string FILE = "$profile:OYB_Core/vehicle_costs.json";

	protected static ref array<string> s_aPrefabs = {};
	protected static ref array<int> s_aCosts = {};
	// Each changed vehicle's cost before any change, to put back.
	protected static ref map<ResourceName, int> s_mOriginal = new map<ResourceName, int>();
	// The loaded prefabs, kept so the changed data isn't unloaded and read fresh.
	protected static ref array<ref Resource> s_aKept = {};

	// On the server, at the start of a game.
	static void LoadAndApply()
	{
		array<string> prefabs = {};
		array<int> costs = {};
		bool exists;
		string text;
		if (!OYB_VehicleSettings.ReadFile(FILE, exists, text))
		{
			PrintFormat("OYB_CORE costs: %1 couldn't be read, no costs changed this game", FILE, level: LogLevel.WARNING);
		}
		else if (exists && !text.Trim().IsEmpty())
		{
			JsonLoadContext context = new JsonLoadContext();
			if (!context.LoadFromString(text) || !context.ReadValue("prefabs", prefabs) || !context.ReadValue("costs", costs)
				|| prefabs.Count() != costs.Count())
			{
				PrintFormat("OYB_CORE costs: %1 isn't a list of prefabs and costs, no costs changed this game", FILE, level: LogLevel.WARNING);
				prefabs.Clear();
				costs.Clear();
			}
		}
		Apply(prefabs, costs);
	}

	static void GetLists(notnull array<string> prefabs, notnull array<int> costs)
	{
		prefabs.Copy(s_aPrefabs);
		costs.Copy(s_aCosts);
	}

	static void Apply(notnull array<string> prefabs, notnull array<int> costs)
	{
		s_aPrefabs.Copy(prefabs);
		s_aCosts.Copy(costs);

		foreach (ResourceName prefab, int original : s_mOriginal)
		{
			int unused;
			if (!prefabs.Contains(prefab))
				SetCost(prefab, original, unused);
		}

		int applied;
		string missed, untaken;
		for (int i = 0; i < Math.Min(prefabs.Count(), costs.Count()); i++)
		{
			if (costs[i] < 0)
				continue;

			int was;
			if (!SetCost(prefabs[i], costs[i], was))
			{
				missed += " " + FilePath.StripPath(prefabs[i]);
				continue;
			}
			if (!s_mOriginal.Contains(prefabs[i]))
				s_mOriginal.Set(prefabs[i], was);
			applied++;

			// What the game reads for it now, made fresh from the data as the cards are.
			Resource resource = Resource.Load(prefabs[i]);
			IEntityComponentSource source = SCR_EditableEntityComponentClass.GetEditableEntitySource(resource);
			if (!source || OYB_VehicleBudget.OwnCampaignCost(SCR_EditableEntityComponentClass.GetInfo(source)) != costs[i])
				untaken += " " + FilePath.StripPath(prefabs[i]);
		}

		PrintFormat("OYB_CORE costs: %1 vehicle costs set for this game", applied);
		if (!untaken.IsEmpty())
			PrintFormat("OYB_CORE costs: set, but the game still reads the old cost for:%1", untaken, level: LogLevel.WARNING);
		if (!missed.IsEmpty())
			PrintFormat("OYB_CORE costs: no building menu cost of their own to change, left as they are:%1", missed, level: LogLevel.WARNING);
	}

	// Sets the campaign entry in the prefab's budget list. was is what it held.
	protected static bool SetCost(ResourceName prefab, int cost, out int was)
	{
		Resource resource = Resource.Load(prefab);
		if (!resource || !resource.IsValid())
			return false;

		IEntityComponentSource source = SCR_EditableEntityComponentClass.GetEditableEntitySource(resource);
		if (!source)
			return false;

		BaseContainer info = source.GetObject("m_UIInfo");
		if (!info)
			return false;

		BaseContainerList list = info.GetObjectArray("m_EntityBudgetCost");
		if (!list)
			return false;

		for (int i = 0; i < list.Count(); i++)
		{
			BaseContainer entry = list.Get(i);
			EEditableEntityBudget type;
			if (!entry || !entry.Get("m_BudgetType", type) || type != EEditableEntityBudget.CAMPAIGN)
				continue;

			entry.Get("m_Value", was);
			if (!entry.Set("m_Value", cost))
				return false;

			if (!s_aKept.Contains(resource))
				s_aKept.Insert(resource);
			return true;
		}
		return false;
	}
}
