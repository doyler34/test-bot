// The vanilla arsenal panel, with OYB's category and search filters applied to
// the items it shows. Cards, icons, supply costs, dragging and equipping stay the
// game's own. The filter bar is looked up by name in the panel's layout; without
// it the panel behaves exactly like vanilla.
modded class SCR_InventoryOpenedStorageArsenalUI
{
	protected static int s_iOYB_Category = OYB_EArsenalCategory.ALL;
	protected static string s_sOYB_Search;

	protected EditBoxWidget m_wOYB_Search;

	override void Init()
	{
		super.Init();

		m_wOYB_Search = EditBoxWidget.Cast(m_widget.FindAnyWidget("OYB_Search"));
		if (m_wOYB_Search)
		{
			m_wOYB_Search.SetText(s_sOYB_Search);
			m_wOYB_Search.AddHandler(this);
		}
	}

	override protected void GetAllItems(out notnull array<IEntity> pItemsInStorage, BaseInventoryStorageComponent pStorage = null)
	{
		super.GetAllItems(pItemsInStorage, pStorage);

		if (!m_bIsArsenal || (s_iOYB_Category == OYB_EArsenalCategory.ALL && s_sOYB_Search.IsEmpty()))
			return;

		if (!m_Storage || !m_Storage.GetOwner())
			return;

		SCR_ArsenalComponent arsenal = SCR_ArsenalComponent.Cast(m_Storage.GetOwner().FindComponent(SCR_ArsenalComponent));
		if (!arsenal)
			return;

		// The panel's items are resolved one per prefab, in the order the arsenal
		// lists them, so the prefab for each item is found by position.
		array<ResourceName> prefabs = {};
		arsenal.GetAvailablePrefabs(prefabs);
		if (prefabs.Count() != pItemsInStorage.Count())
		{
			PrintFormat("OYB_ARSENAL filter skipped: %1 items shown but %2 offered", pItemsInStorage.Count(), prefabs.Count());
			return;
		}

		OYB_ArsenalCatalogService catalog = OYB_ArsenalCatalogService.GetInstance();
		array<IEntity> kept = {};
		foreach (int i, IEntity item : pItemsInStorage)
		{
			OYB_ArsenalEntry entry = catalog.FindEntry(arsenal, prefabs[i]);
			if (!entry)
				continue;
			if (s_iOYB_Category != OYB_EArsenalCategory.ALL && entry.m_iCategory != s_iOYB_Category)
				continue;
			if (!s_sOYB_Search.IsEmpty() && !entry.m_sSearch.Contains(s_sOYB_Search))
				continue;
			kept.Insert(item);
		}

		pItemsInStorage.Clear();
		pItemsInStorage.InsertAll(kept);
	}

	void OYB_SetCategory(int category)
	{
		if (category == s_iOYB_Category)
			return;

		s_iOYB_Category = category;
		OYB_ApplyFilters();
	}

	void OYB_SetSearch(string search)
	{
		search.TrimInPlace();
		search.ToLower();
		if (search == s_sOYB_Search)
			return;

		s_sOYB_Search = search;
		OYB_ApplyFilters();
	}

	protected void OYB_ApplyFilters()
	{
		m_iLastShownPage = 0;
		Refresh();
	}

	override bool OnChange(Widget w, bool finished)
	{
		if (w && w == m_wOYB_Search)
		{
			OYB_SetSearch(m_wOYB_Search.GetText());
			return true;
		}
		return super.OnChange(w, finished);
	}
}
