// Limits on live vehicles per side, by group: several vehicles (an Mi-8 and its
// variants, say, or every helicopter) can share one allowance. Set on the OYB
// panel and only ever decided on the server.
//
// - Ownership: a vehicle placed from the building menu belongs to the side of
//   the player who placed it, and one bought at a depot to the depot's side
//   (the game only lets a player buy at their own side's depot), whoever
//   drives it later.
// - Counted: live vehicles of a limited group, empty or abandoned ones too.
//   A destroyed (wrecked) or deleted vehicle stops counting.
// - Not counted: Game Master vehicles, map-placed vehicles and HQ starting
//   vehicles, except that after the server starts (fresh or from a save) every
//   live vehicle of a limited group is counted once for its own side, as the
//   game doesn't remember which side bought what.
// - A lowered limit never removes vehicles; it only stops new ones.
//
// Settings (from OYB_VehicleSettings): limits on or off; each group's limit,
// where -1 is unlimited and 0 allows none; and which exact prefabs are in which
// group. A vehicle's variants (the depot's, or the building menu's random
// ones) count as the vehicle. A prefab in no group counts in its category's
// allowance when the category has a limit (Helicopters, Cars...), else it's
// unlimited.
class OYB_VehicleLimits
{
	static const int UNLIMITED = -1;

	// A category's allowance is kept under its name with this in front, so it
	// can't clash with a group of the same name.
	static const string CATEGORY_KEY = "#";

	protected static bool s_bEnabled;
	protected static ref map<string, int> s_mGroupLimits = new map<string, int>();
	// Category -> limit, for every vehicle of the category in no group.
	protected static ref map<string, int> s_mCategoryLimits = new map<string, int>();
	// Prefab -> category, from the vehicle list.
	protected static ref map<ResourceName, string> s_mPrefabCategory = new map<ResourceName, string>();
	protected static ref map<ResourceName, string> s_mPrefabGroups = new map<ResourceName, string>();
	// Variant -> the vehicle it's a variant of (depot or building menu variants).
	protected static ref map<ResourceName, ResourceName> s_mVariantOf = new map<ResourceName, ResourceName>();

	// Live vehicles being followed, with the side each counts for. The group
	// comes from the vehicle's prefab when counting, so a group changed on the
	// panel applies straight away.
	protected static ref array<IEntity> s_aVehicles = {};
	protected static ref array<string> s_aFactions = {};
	// Of those, the ones already in the world when the server started.
	protected static ref array<IEntity> s_aAtStart = {};

	protected static bool s_bStarted;

	static void Configure(bool enabled, notnull array<string> groupNames, notnull array<int> groupLimits, notnull array<string> groupItems, notnull array<string> groupOf,
		notnull array<string> categoryNames, notnull array<int> categoryLimits)
	{
		s_bEnabled = enabled;

		s_mCategoryLimits.Clear();
		for (int c = 0; c < Math.Min(categoryNames.Count(), categoryLimits.Count()); c++)
		{
			if (!categoryNames[c].IsEmpty() && categoryLimits[c] != UNLIMITED)
				s_mCategoryLimits.Set(categoryNames[c], Math.Max(categoryLimits[c], UNLIMITED));
		}

		s_mGroupLimits.Clear();
		for (int i = 0; i < Math.Min(groupNames.Count(), groupLimits.Count()); i++)
		{
			if (!groupNames[i].IsEmpty())
				s_mGroupLimits.Set(groupNames[i], Math.Max(groupLimits[i], UNLIMITED));
		}

		s_mPrefabGroups.Clear();
		for (int m = 0; m < Math.Min(groupItems.Count(), groupOf.Count()); m++)
		{
			if (!groupItems[m].IsEmpty() && s_mGroupLimits.Contains(groupOf[m]))
				s_mPrefabGroups.Set(groupItems[m], groupOf[m]);
		}
	}

	static void SetVariant(ResourceName variant, ResourceName vehicle)
	{
		s_mVariantOf.Set(variant, vehicle);
	}

	static void SetCategory(ResourceName prefab, string category)
	{
		if (!prefab.IsEmpty() && !category.IsEmpty())
			s_mPrefabCategory.Set(prefab, category);
	}

	// What a group is called in a message: a category's allowance by the
	// category's name.
	static string DisplayName(string group)
	{
		if (group.StartsWith(CATEGORY_KEY))
			return group.Substring(CATEGORY_KEY.Length(), group.Length() - CATEGORY_KEY.Length());

		return group;
	}

	// The vehicle a variant belongs to, or empty.
	static ResourceName VariantOf(ResourceName prefab)
	{
		ResourceName vehicle;
		s_mVariantOf.Find(prefab, vehicle);
		return vehicle;
	}

	// The group a prefab counts in, or empty for none: its own group, its
	// vehicle's when it's a variant, or else its category's allowance when the
	// category has a limit.
	static string GroupOf(ResourceName prefab)
	{
		string group;
		if (s_mPrefabGroups.Find(prefab, group))
			return group;

		ResourceName vehicle;
		if (s_mVariantOf.Find(prefab, vehicle) && s_mPrefabGroups.Find(vehicle, group))
			return group;

		string category;
		if (!s_mPrefabCategory.Find(prefab, category) && !vehicle.IsEmpty())
			s_mPrefabCategory.Find(vehicle, category);
		if (!category.IsEmpty() && s_mCategoryLimits.Contains(category))
			return CATEGORY_KEY + category;

		return string.Empty;
	}

	protected static bool LimitOf(string group, out int limit)
	{
		if (group.StartsWith(CATEGORY_KEY))
			return s_mCategoryLimits.Find(DisplayName(group), limit);

		return s_mGroupLimits.Find(group, limit);
	}

	// Whether one more of this vehicle is allowed for the side; when it isn't,
	// the group, its live count and its limit, for the message. A limited
	// vehicle whose side isn't known is refused rather than let through.
	static bool Allows(string factionKey, ResourceName prefab, out string group, out int count, out int limit)
	{
		group = GroupOf(prefab);
		if (!s_bEnabled || group.IsEmpty() || !LimitOf(group, limit) || limit == UNLIMITED)
			return true;

		if (factionKey.IsEmpty())
			return false;

		count = Count(factionKey, group);
		return count < limit;
	}

	// Followed once; a vehicle already followed keeps its side.
	static void Register(IEntity vehicle, string factionKey)
	{
		if (!vehicle || factionKey.IsEmpty() || s_aVehicles.Contains(vehicle))
			return;

		s_aVehicles.Insert(vehicle);
		s_aFactions.Insert(factionKey);
	}

	// The vehicles counted for the side in the group, for the log: each one's
	// prefab and position, and whether it was already in the world when the
	// server started.
	static string Counted(string factionKey, string group)
	{
		Prune();

		string text;
		foreach (int i, IEntity vehicle : s_aVehicles)
		{
			if (s_aFactions[i] != factionKey || GroupOf(SCR_ResourceNameUtils.GetPrefabName(vehicle)) != group)
				continue;

			if (!text.IsEmpty())
				text += "; ";
			text += FilePath.StripPath(SCR_ResourceNameUtils.GetPrefabName(vehicle).GetPath()) + " at " + vehicle.GetOrigin().ToString();
			if (s_aAtStart.Contains(vehicle))
				text += " (in the world at start)";
		}
		return text;
	}

	static int Count(string factionKey, string group)
	{
		Prune();

		int count;
		foreach (int i, IEntity vehicle : s_aVehicles)
		{
			if (s_aFactions[i] == factionKey && GroupOf(SCR_ResourceNameUtils.GetPrefabName(vehicle)) == group)
				count++;
		}
		return count;
	}

	// Drops deleted and destroyed vehicles. Each vehicle is in the list once, so
	// it can only ever stop counting once.
	protected static void Prune()
	{
		for (int i = s_aVehicles.Count() - 1; i >= 0; i--)
		{
			IEntity vehicle = s_aVehicles[i];
			if (vehicle && !vehicle.IsDeleted() && !IsWreck(vehicle))
				continue;

			s_aVehicles.Remove(i);
			s_aFactions.Remove(i);
		}

		for (int k = s_aAtStart.Count() - 1; k >= 0; k--)
		{
			if (!s_aVehicles.Contains(s_aAtStart[k]))
				s_aAtStart.Remove(k);
		}
	}

	protected static bool IsWreck(IEntity vehicle)
	{
		DamageManagerComponent damage = DamageManagerComponent.Cast(vehicle.FindComponent(DamageManagerComponent));
		return damage && damage.IsDestroyed();
	}

	protected static void OnVehicleDamageStateChanged(SCR_VehicleDamageManagerComponent damage)
	{
		if (damage && damage.IsDestroyed())
			Prune();
	}

	// A new game: forgets the last game's vehicles and stops following its
	// wrecks.
	static void ResetSession()
	{
		SCR_VehicleDamageManagerComponent.GetOnVehicleDamageStateChanged().Remove(OnVehicleDamageStateChanged);
		s_aVehicles.Clear();
		s_aFactions.Clear();
		s_aAtStart.Clear();
		s_bStarted = false;
	}

	// When the server starts: follows vehicles being wrecked, and follows every
	// live vehicle already in the world (from a save, or placed) for its own
	// side. False while the editor core isn't up or a saved game is still
	// loading, to be tried again.
	static bool StartServer()
	{
		if (s_bStarted || !Replication.IsServer())
			return true;

		SCR_EditableEntityCore core = SCR_EditableEntityCore.Cast(SCR_EditableEntityCore.GetInstance(SCR_EditableEntityCore));
		if (!core || SCR_PersistenceSystem.IsLoadInProgress())
			return false;
		s_bStarted = true;

		SCR_VehicleDamageManagerComponent.GetOnVehicleDamageStateChanged().Remove(OnVehicleDamageStateChanged);
		SCR_VehicleDamageManagerComponent.GetOnVehicleDamageStateChanged().Insert(OnVehicleDamageStateChanged);

		set<SCR_EditableEntityComponent> entities = new set<SCR_EditableEntityComponent>();
		core.GetAllEntities(entities);
		int counted;
		foreach (SCR_EditableEntityComponent editable : entities)
		{
			if (!editable || editable.GetEntityType() != EEditableEntityType.VEHICLE)
				continue;

			IEntity vehicle = editable.GetOwner();
			if (!vehicle || !vehicle.GetPrefabData() || IsWreck(vehicle))
				continue;

			FactionAffiliationComponent affiliation = FactionAffiliationComponent.Cast(vehicle.FindComponent(FactionAffiliationComponent));
			if (!affiliation || affiliation.GetDefaultFactionKey().IsEmpty())
				continue;

			if (s_aVehicles.Contains(vehicle))
				continue;

			Register(vehicle, affiliation.GetDefaultFactionKey());
			s_aAtStart.Insert(vehicle);
			counted++;
		}
		PrintFormat("OYB_CORE vehicle limits: following %1 vehicles already in the world", counted);
		return true;
	}
}
