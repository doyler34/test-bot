// The vanilla arsenal panel, with an OYB filter bar above its item grid:
// previous / next category with controller prompts, the category name and count,
// and a search box. The bar is built from the game's own widget library layouts so
// it matches the rest of the inventory. Cards, icons, supply costs, dragging and
// equipping stay the game's own.
modded class SCR_InventoryOpenedStorageArsenalUI
{
	protected static const ResourceName OYB_NAV_BUTTON_LAYOUT = "{08CF3B69CB1ACBC4}UI/layouts/WidgetLibrary/WLib_NavigationButton.layout";
	protected static const ResourceName OYB_LABEL_LAYOUT = "{829BC1189A899017}UI/layouts/WidgetLibrary/WLibRefined/WLib_Label.layout";
	protected static const ResourceName OYB_EDIT_BOX_LAYOUT = "{0022F0B45ADBC5AC}UI/layouts/WidgetLibrary/EditBox/WLib_EditBox.layout";

	protected static int s_iOYB_Category = OYB_EArsenalCategory.ALL;
	protected static string s_sOYB_Search;
	protected static bool s_bOYB_TreeLogged;

	protected TextWidget m_wOYB_CategoryText;
	protected SCR_EditBoxComponent m_OYB_Search;

	override void Init()
	{
		super.Init();

		if (!s_bOYB_TreeLogged)
		{
			s_bOYB_TreeLogged = true;
			OYB_LogTree(m_widget, 0);
		}

		OYB_BuildFilterBar();
	}

	//------------------------------------------------------------------------------------------------
	// Filtering

	override protected void GetAllItems(out notnull array<IEntity> pItemsInStorage, BaseInventoryStorageComponent pStorage = null)
	{
		super.GetAllItems(pItemsInStorage, pStorage);

		if (!m_bIsArsenal || (s_iOYB_Category == OYB_EArsenalCategory.ALL && s_sOYB_Search.IsEmpty()))
			return;

		SCR_ArsenalComponent arsenal = OYB_GetArsenal();
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

	protected SCR_ArsenalComponent OYB_GetArsenal()
	{
		if (!m_Storage || !m_Storage.GetOwner())
			return null;

		return SCR_ArsenalComponent.Cast(m_Storage.GetOwner().FindComponent(SCR_ArsenalComponent));
	}

	void OYB_SetCategory(int category)
	{
		if (category == s_iOYB_Category)
			return;

		s_iOYB_Category = category;
		OYB_UpdateCategoryText();
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

	//------------------------------------------------------------------------------------------------
	// Filter bar

	// Init runs before the panel's items are read, so m_bIsArsenal isn't set yet;
	// this class is only created for arsenals anyway.
	protected void OYB_BuildFilterBar()
	{
		if (!m_wGrid)
		{
			Print("OYB_ARSENAL no item grid found, filter bar not added", LogLevel.WARNING);
			return;
		}

		Widget gridParent = m_wGrid.GetParent();
		if (!gridParent)
			return;

		WorkspaceWidget workspace = GetGame().GetWorkspace();
		Widget bar = workspace.CreateWidget(WidgetType.HorizontalLayoutWidgetTypeID, WidgetFlags.VISIBLE, null, 0, gridParent);
		if (!bar)
			return;
		bar.SetName("OYB_FilterBar");
		OYB_MoveBefore(bar, m_wGrid);

		SCR_InputButtonComponent previous = OYB_CreateNavButton(bar, "MenuTabLeft", "Previous");
		if (previous)
			previous.m_OnActivated.Insert(OYB_OnPreviousCategory);

		Widget label = workspace.CreateWidgets(OYB_LABEL_LAYOUT, bar);
		if (label)
		{
			m_wOYB_CategoryText = TextWidget.Cast(OYB_FindWidgetOfType(label, TextWidget));
			AlignableSlot.SetVerticalAlign(label, LayoutVerticalAlign.Center);
			AlignableSlot.SetPadding(label, 8, 0, 8, 0);
		}

		SCR_InputButtonComponent next = OYB_CreateNavButton(bar, "MenuTabRight", "Next");
		if (next)
			next.m_OnActivated.Insert(OYB_OnNextCategory);

		Widget search = workspace.CreateWidgets(OYB_EDIT_BOX_LAYOUT, bar);
		if (search)
		{
			LayoutSlot.SetSizeMode(search, LayoutSizeMode.Fill);
			AlignableSlot.SetPadding(search, 12, 0, 0, 0);
			m_OYB_Search = SCR_EditBoxComponent.Cast(OYB_FindHandler(search, SCR_EditBoxComponent));
			if (m_OYB_Search)
			{
				m_OYB_Search.SetPlaceholderText("Search");
				m_OYB_Search.SetValue(s_sOYB_Search);
				// Applied when typing ends, so refreshing the grid never takes
				// focus away from the box mid-word.
				m_OYB_Search.m_OnConfirm.Insert(OYB_OnSearchConfirmed);
				m_OYB_Search.m_OnWriteModeLeave.Insert(OYB_OnSearchLeft);
			}
		}

		OYB_UpdateCategoryText();
	}

	protected SCR_InputButtonComponent OYB_CreateNavButton(Widget parent, string action, string label)
	{
		Widget root = GetGame().GetWorkspace().CreateWidgets(OYB_NAV_BUTTON_LAYOUT, parent);
		if (!root)
			return null;

		AlignableSlot.SetVerticalAlign(root, LayoutVerticalAlign.Center);
		SCR_InputButtonComponent button = SCR_InputButtonComponent.Cast(root.FindHandler(SCR_InputButtonComponent));
		if (!button)
			return null;

		button.SetAction(action, forceUpdate: true);
		button.SetLabel(label);
		return button;
	}

	protected void OYB_UpdateCategoryText()
	{
		if (!m_wOYB_CategoryText)
			return;

		int count;
		SCR_ArsenalComponent arsenal = OYB_GetArsenal();
		if (arsenal)
		{
			bool fromCache;
			array<ref OYB_ArsenalEntry> entries = OYB_ArsenalCatalogService.GetInstance().GetEntries(arsenal, fromCache);
			foreach (OYB_ArsenalEntry entry : entries)
			{
				if (s_iOYB_Category == OYB_EArsenalCategory.ALL || entry.m_iCategory == s_iOYB_Category)
					count++;
			}
		}

		m_wOYB_CategoryText.SetText(string.Format("%1 (%2)", OYB_ArsenalCategories.GetName(s_iOYB_Category), count));
	}

	protected void OYB_OnPreviousCategory(SCR_InputButtonComponent button, string action)
	{
		OYB_SetCategory((s_iOYB_Category + OYB_ArsenalCategories.COUNT - 1) % OYB_ArsenalCategories.COUNT);
	}

	protected void OYB_OnNextCategory(SCR_InputButtonComponent button, string action)
	{
		OYB_SetCategory((s_iOYB_Category + 1) % OYB_ArsenalCategories.COUNT);
	}

	protected void OYB_OnSearchConfirmed(SCR_EditBoxComponent box, string text)
	{
		OYB_SetSearch(text);
	}

	protected void OYB_OnSearchLeft(string text)
	{
		OYB_SetSearch(text);
	}

	//------------------------------------------------------------------------------------------------
	// Widget helpers

	// AddChild appends, so the widgets from the target onwards are moved to the
	// end after the new one, which keeps the parent's order otherwise unchanged.
	protected void OYB_MoveBefore(Widget widget, Widget target)
	{
		Widget parent = target.GetParent();
		array<Widget> after = {};
		Widget child = parent.GetChildren();
		bool found;
		while (child)
		{
			if (child == target)
				found = true;
			if (found && child != widget)
				after.Insert(child);
			child = child.GetSibling();
		}

		foreach (Widget moved : after)
		{
			parent.RemoveChild(moved);
			parent.AddChild(moved);
		}
	}

	protected Widget OYB_FindWidgetOfType(Widget root, typename type)
	{
		if (root.IsInherited(type))
			return root;

		Widget child = root.GetChildren();
		while (child)
		{
			Widget found = OYB_FindWidgetOfType(child, type);
			if (found)
				return found;
			child = child.GetSibling();
		}
		return null;
	}

	protected ScriptedWidgetEventHandler OYB_FindHandler(Widget root, typename type)
	{
		ScriptedWidgetEventHandler handler = root.FindHandler(type);
		if (handler)
			return handler;

		Widget child = root.GetChildren();
		while (child)
		{
			handler = OYB_FindHandler(child, type);
			if (handler)
				return handler;
			child = child.GetSibling();
		}
		return null;
	}

	// Logged once, so the panel's real structure is on record for the next layout pass.
	protected void OYB_LogTree(Widget w, int depth)
	{
		if (!w || depth > 5)
			return;

		string indent;
		for (int i = 0; i < depth; i++)
			indent += "  ";
		PrintFormat("OYB_ARSENAL ui %1%2 (%3)", indent, w.GetName(), w.GetTypeName());

		Widget child = w.GetChildren();
		while (child)
		{
			OYB_LogTree(child, depth + 1);
			child = child.GetSibling();
		}
	}
}
