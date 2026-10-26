// Whenever an inventory panel shows an arsenal, OYB adds a filter bar above its
// item grid: previous / next category with controller prompts, the category name
// and count, and a search box. Arsenals are shown by the Vicinity panel (after
// stepping into the box) or as an opened storage; both list their items through
// this base class, so this is where the filter goes. The bar is built from the
// game's own widget library layouts so it matches the rest of the inventory.
// Cards, icons, supply costs, dragging and equipping stay the game's own.
modded class SCR_InventoryStorageBaseUI
{
	protected static const ResourceName OYB_NAV_BUTTON_LAYOUT = "{08CF3B69CB1ACBC4}UI/layouts/WidgetLibrary/WLib_NavigationButton.layout";
	protected static const ResourceName OYB_LABEL_LAYOUT = "{829BC1189A899017}UI/layouts/WidgetLibrary/WLibRefined/WLib_Label.layout";
	protected static const ResourceName OYB_EDIT_BOX_LAYOUT = "{0022F0B45ADBC5AC}UI/layouts/WidgetLibrary/EditBox/WLib_EditBox.layout";
	protected static const ResourceName OYB_TAB_BUTTON_LAYOUT = "{D27D044A8145DC7C}UI/layouts/WidgetLibrary/Buttons/WLib_ButtonTextUnderlined.layout";

	protected static int s_iOYB_Category = OYB_EArsenalCategory.ALL;
	protected static string s_sOYB_Search;
	protected static bool s_bOYB_TreeLogged;

	protected Widget m_wOYB_FilterBar;
	protected TextWidget m_wOYB_CategoryText;
	protected SCR_EditBoxComponent m_OYB_Search;
	protected ref array<SCR_ButtonTextComponent> m_aOYB_CategoryButtons = {};
	protected ref array<int> m_aOYB_CategoryCounts = {};
	protected SCR_ArsenalComponent m_OYB_Arsenal;

	//------------------------------------------------------------------------------------------------
	// Filtering

	override protected void GetAllItems(out notnull array<IEntity> pItemsInStorage, BaseInventoryStorageComponent pStorage = null)
	{
		super.GetAllItems(pItemsInStorage, pStorage);
		OYB_FilterArsenalItems(pItemsInStorage, pStorage);
	}

	protected void OYB_FilterArsenalItems(notnull array<IEntity> pItemsInStorage, BaseInventoryStorageComponent pStorage)
	{
		// A refresh lists other storages too, so whether the bar shows depends on
		// what the panel is showing, not on which list this happens to be.
		OYB_UpdateFilterBarVisibility();

		SCR_ArsenalComponent listed = OYB_GetArsenal(pStorage);
		if (!listed)
			return;

		m_OYB_Arsenal = listed;
		OYB_UpdateCategoryText();

		if (s_iOYB_Category == OYB_EArsenalCategory.ALL && s_sOYB_Search.IsEmpty())
			return;

		SCR_ArsenalComponent arsenal = m_OYB_Arsenal;

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

	// Only the storages the game itself lists as arsenal items, the same test its
	// own GetAllItems uses.
	protected SCR_ArsenalComponent OYB_GetArsenal(BaseInventoryStorageComponent storage)
	{
		if (!storage || !storage.GetOwner())
			return null;

		IEntity owner = storage.GetOwner();
		if (!owner.FindComponent(SCR_ArsenalInventoryStorageManagerComponent))
			return null;

		return SCR_ArsenalComponent.Cast(owner.FindComponent(SCR_ArsenalComponent));
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

	//------------------------------------------------------------------------------------------------
	// Filter bar

	protected void OYB_UpdateFilterBarVisibility()
	{
		OYB_ShowFilterBar(OYB_GetArsenal(GetCurrentNavigationStorage()) != null);
	}

	protected void OYB_ShowFilterBar(bool show)
	{
		if (show && !m_wOYB_FilterBar)
			OYB_BuildFilterBar();

		if (m_wOYB_FilterBar)
			m_wOYB_FilterBar.SetVisible(show);
	}

	protected void OYB_BuildFilterBar()
	{
		if (!s_bOYB_TreeLogged)
		{
			s_bOYB_TreeLogged = true;
			PrintFormat("OYB_ARSENAL arsenal shown in %1, panel structure:", ClassName());
			OYB_LogTree(m_widget, 0);
		}

		if (!m_wGrid || !m_wGrid.GetParent())
		{
			Print("OYB_ARSENAL no item grid found, filter bar not added", LogLevel.WARNING);
			return;
		}

		// Two rows: the category switcher, then a full-width search box. The panel
		// is only three cards wide, so one row squeezes the search box.
		WorkspaceWidget workspace = GetGame().GetWorkspace();
		Widget bar = workspace.CreateWidget(WidgetType.VerticalLayoutWidgetTypeID, WidgetFlags.VISIBLE, null, 0, m_wGrid.GetParent());
		if (!bar)
			return;
		bar.SetName("OYB_FilterBar");
		AlignableSlot.SetHorizontalAlign(bar, LayoutHorizontalAlign.Stretch);
		AlignableSlot.SetPadding(bar, 0, 4, 0, 4);
		OYB_MoveBefore(bar, m_wGrid);
		m_wOYB_FilterBar = bar;
		PrintFormat("OYB_ARSENAL filter bar added above %1 in %2", m_wGrid.GetName(), m_wGrid.GetParent().GetName());

		Widget categoryRow = workspace.CreateWidget(WidgetType.HorizontalLayoutWidgetTypeID, WidgetFlags.VISIBLE, null, 0, bar);
		AlignableSlot.SetHorizontalAlign(categoryRow, LayoutHorizontalAlign.Stretch);

		// The inventory already uses MenuTabLeft / MenuTabRight (Q / E, LB / RB) to
		// turn the grid's pages, so categories cycle on MenuFilter instead.
		SCR_InputButtonComponent cycle = OYB_CreateNavButton(categoryRow, "MenuFilter", "Category");
		if (cycle)
			cycle.m_OnActivated.Insert(OYB_OnNextCategory);

		Widget label = workspace.CreateWidgets(OYB_LABEL_LAYOUT, categoryRow);
		if (label)
		{
			m_wOYB_CategoryText = TextWidget.Cast(OYB_FindWidgetOfType(label, TextWidget));
			LayoutSlot.SetSizeMode(label, LayoutSizeMode.Fill);
			AlignableSlot.SetHorizontalAlign(label, LayoutHorizontalAlign.Center);
			AlignableSlot.SetVerticalAlign(label, LayoutVerticalAlign.Center);
		}

		OYB_KeepCreationOrder(categoryRow);

		Widget search = workspace.CreateWidgets(OYB_EDIT_BOX_LAYOUT, bar);
		if (search)
		{
			AlignableSlot.SetHorizontalAlign(search, LayoutHorizontalAlign.Stretch);
			AlignableSlot.SetPadding(search, 0, 4, 0, 0);
			m_OYB_Search = SCR_EditBoxComponent.Cast(OYB_FindHandler(search, SCR_EditBoxComponent));
			if (m_OYB_Search)
			{
				m_OYB_Search.UseLabel(false);
				m_OYB_Search.SetPlaceholderText("Search items");
				m_OYB_Search.SetValue(s_sOYB_Search);
				// Applied when typing ends, so refreshing the grid never takes
				// focus away from the box mid-word.
				m_OYB_Search.m_OnConfirm.Insert(OYB_OnSearchConfirmed);
				m_OYB_Search.m_OnWriteModeLeave.Insert(OYB_OnSearchLeft);
			}
		}

		OYB_BuildCategoryColumn();

	}

	// A column of category buttons to the right of the item grid. The grid is
	// moved into a row with the column so the rest of the panel is untouched.
	protected void OYB_BuildCategoryColumn()
	{
		Widget container = m_wGrid.GetParent();
		WorkspaceWidget workspace = GetGame().GetWorkspace();

		Widget row = workspace.CreateWidget(WidgetType.HorizontalLayoutWidgetTypeID, WidgetFlags.VISIBLE, null, 0, container);
		if (!row)
			return;
		row.SetName("OYB_GridRow");
		OYB_MoveBefore(row, m_wGrid);
		// A widget with nothing holding it is deleted when it's taken out of its
		// parent; the member is a weak reference, so a local keeps it alive.
		Widget grid = m_wGrid;
		container.RemoveChild(grid);
		row.AddChild(grid);

		Widget column = workspace.CreateWidget(WidgetType.VerticalLayoutWidgetTypeID, WidgetFlags.VISIBLE, null, 0, row);
		if (!column)
			return;
		column.SetName("OYB_CategoryColumn");
		AlignableSlot.SetPadding(column, 8, 0, 0, 0);
		AlignableSlot.SetVerticalAlign(column, LayoutVerticalAlign.Top);

		for (int category = 0; category < OYB_ArsenalCategories.COUNT; category++)
		{
			Widget buttonRoot = workspace.CreateWidgets(OYB_TAB_BUTTON_LAYOUT, column);
			SCR_ButtonTextComponent button = null;
			if (buttonRoot)
			{
				AlignableSlot.SetHorizontalAlign(buttonRoot, LayoutHorizontalAlign.Stretch);
				AlignableSlot.SetPadding(buttonRoot, 0, 1, 0, 1);
				button = SCR_ButtonTextComponent.Cast(buttonRoot.FindHandler(SCR_ButtonTextComponent));
			}

			if (button)
			{
				button.SetText(OYB_ArsenalCategories.GetName(category));
				button.SetToggleable(true);
				button.m_OnClicked.Insert(OYB_OnCategoryClicked);
			}
			m_aOYB_CategoryButtons.Insert(button);
		}
	}

	protected void OYB_OnCategoryClicked(SCR_ButtonBaseComponent clicked)
	{
		foreach (int category, SCR_ButtonTextComponent button : m_aOYB_CategoryButtons)
		{
			if (button && button == clicked)
			{
				OYB_SetCategory(category);
				break;
			}
		}

		// A toggle button flips itself on click; show the real selection again.
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
		array<int> counts = m_aOYB_CategoryCounts;
		counts.Clear();
		for (int c = 0; c < OYB_ArsenalCategories.COUNT; c++)
			counts.Insert(0);

		if (m_OYB_Arsenal)
		{
			bool fromCache;
			array<ref OYB_ArsenalEntry> entries = OYB_ArsenalCatalogService.GetInstance().GetEntries(m_OYB_Arsenal, fromCache);
			foreach (OYB_ArsenalEntry entry : entries)
				counts[entry.m_iCategory] = counts[entry.m_iCategory] + 1;
			counts[OYB_EArsenalCategory.ALL] = entries.Count();
		}

		if (m_wOYB_CategoryText)
			m_wOYB_CategoryText.SetText(string.Format("%1 (%2)", OYB_ArsenalCategories.GetName(s_iOYB_Category), counts[s_iOYB_Category]));

		// Categories this arsenal has nothing in get no button.
		foreach (int category, SCR_ButtonTextComponent button : m_aOYB_CategoryButtons)
		{
			if (!button)
				continue;

			bool hasItems = category == OYB_EArsenalCategory.ALL || counts[category] > 0;
			button.GetRootWidget().SetVisible(hasItems);
			button.SetText(string.Format("%1 (%2)", OYB_ArsenalCategories.GetName(category), counts[category]));
			button.SetToggled(category == s_iOYB_Category, false, false, true);
		}
	}

	// Skips categories this arsenal has nothing in, like the button column does.
	protected void OYB_OnNextCategory(SCR_InputButtonComponent button, string action)
	{
		int category = s_iOYB_Category;
		for (int i = 0; i < OYB_ArsenalCategories.COUNT; i++)
		{
			category = (category + 1) % OYB_ArsenalCategories.COUNT;
			if (category == OYB_EArsenalCategory.ALL || !m_aOYB_CategoryCounts.IsIndexValid(category) || m_aOYB_CategoryCounts[category] > 0)
				break;
		}
		OYB_SetCategory(category);
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

	// Layouts sort children by z-order, and widgets made from layout files bring
	// their own, so the order they were added in is set explicitly.
	protected void OYB_KeepCreationOrder(Widget parent)
	{
		int order;
		Widget child = parent.GetChildren();
		while (child)
		{
			child.SetZOrder(order);
			order++;
			child = child.GetSibling();
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

// The Vicinity panel lists what's around the player itself without going through
// the base class, so the bar's visibility is checked here too.
modded class SCR_InventoryStorageLootUI
{
	override protected void GetAllItems(out notnull array<IEntity> pItemsInStorage, BaseInventoryStorageComponent pStorage = null)
	{
		super.GetAllItems(pItemsInStorage, pStorage);

		if (!pStorage)
			OYB_UpdateFilterBarVisibility();
	}
}
