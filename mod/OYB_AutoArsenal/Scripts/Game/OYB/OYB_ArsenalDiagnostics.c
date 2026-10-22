// Counts everything the game has registered in its arsenal catalogues, ten seconds
// after the mission starts, and writes it to the log with OYB_ARSENAL in front.
// It only reads. It changes no catalogue, price or rank, and it doesn't touch Bacon.
//
// The names this uses that are not copied from the mortar mod (which has run in the
// game): SCR_EntityCatalogManagerComponent, GetAllArsenalItems, SCR_ArsenalItem,
// GetItemResourceName, GetItemType and GetItemMode. If Workbench shows a red error
// about one of them, the message names the line: send it over and it gets fixed.
modded class SCR_EntityCatalogManagerComponent
{
	protected bool m_bOYB_ArsenalDone;

	override void OnPostInit(IEntity owner)
	{
		super.OnPostInit(owner);
		if (m_bOYB_ArsenalDone)
			return;
		m_bOYB_ArsenalDone = true;
		GetGame().GetCallqueue().CallLater(OYB_ArsenalReport, 10000, false);
	}

	protected void OYB_ArsenalReport()
	{
		array<SCR_ArsenalItem> items = {};
		GetAllArsenalItems(items);

		map<string, bool> seen = new map<string, bool>();
		map<int, int> byType = new map<int, int>();
		map<int, int> byMode = new map<int, int>();
		int duplicates = 0;
		int empty = 0;
		int bacon = 0;
		int samples = 0;

		foreach (SCR_ArsenalItem item : items)
		{
			if (!item)
				continue;

			string prefab = item.GetItemResourceName();
			if (prefab == string.Empty)
			{
				empty++;
				continue;
			}

			if (seen.Contains(prefab))
			{
				duplicates++;
				continue;
			}
			seen.Insert(prefab, true);

			int type = item.GetItemType();
			if (byType.Contains(type))
				byType.Set(type, byType.Get(type) + 1);
			else
				byType.Insert(type, 1);

			int mode = item.GetItemMode();
			if (byMode.Contains(mode))
				byMode.Set(mode, byMode.Get(mode) + 1);
			else
				byMode.Insert(mode, 1);

			string lower = prefab;
			lower.ToLower();
			if (lower.Contains("bacon") || lower.Contains("loadout"))
			{
				bacon++;
				PrintFormat("OYB_ARSENAL bacon-looking prefab: %1", prefab);
			}

			// A short sample only, so a busy server's log stays readable.
			if (samples < 25)
			{
				PrintFormat("OYB_ARSENAL sample: %1 type=%2 mode=%3", prefab, type, mode);
				samples++;
			}
		}

		PrintFormat("OYB_ARSENAL registered entries: %1", items.Count());
		PrintFormat("OYB_ARSENAL unique prefabs: %1", seen.Count());
		PrintFormat("OYB_ARSENAL duplicate references: %1", duplicates);
		PrintFormat("OYB_ARSENAL entries with no prefab: %1", empty);
		PrintFormat("OYB_ARSENAL prefabs with bacon or loadout in the name: %1", bacon);
		foreach (int t, int count : byType)
			PrintFormat("OYB_ARSENAL items of type %1: %2", t, count);
		foreach (int m, int count2 : byMode)
			PrintFormat("OYB_ARSENAL items of mode %1: %2", m, count2);
		Print("OYB_ARSENAL done. Diagnostic only: nothing was changed.");
	}
}
