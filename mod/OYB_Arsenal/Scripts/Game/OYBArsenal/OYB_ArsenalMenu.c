class OYB_ArsenalRow
{
	ButtonWidget m_wButton;
	ImageWidget m_wBackground;
	TextWidget m_wText;
	int m_iIndex = -1;
}

// Phase 1: shows what the arsenal offers, by category, with search and paging.
// It reads only. Nothing is equipped, spawned or charged from here yet.
//
// Everything inside the window is built in script, so the only layout this needs
// is an empty one made in Workbench (see the README).
class OYB_ArsenalMenu : ChimeraMenuBase
{
	protected static const int PAGE_SIZE = 18;
	protected static const float PANEL_WIDTH = 1600;
	protected static const float PANEL_HEIGHT = 880;
	protected static const float ARSENAL_CHECK_INTERVAL = 0.5;

	protected static SCR_ArsenalComponent s_PendingArsenal;

	protected ref Color m_ColorDim = new Color(0, 0, 0, 0.6);
	protected ref Color m_ColorPanel = new Color(0.09, 0.10, 0.11, 0.96);
	protected ref Color m_ColorRow = new Color(0.16, 0.18, 0.20, 1);
	protected ref Color m_ColorFocus = new Color(0.45, 0.48, 0.32, 1);
	protected ref Color m_ColorSelected = new Color(0.29, 0.31, 0.21, 1);
	protected ref Color m_ColorText = new Color(0.93, 0.92, 0.88, 1);
	protected ref Color m_ColorMuted = new Color(0.62, 0.63, 0.58, 1);
	protected ref Color m_ColorAccent = new Color(0.74, 0.72, 0.52, 1);

	protected SCR_ArsenalComponent m_Arsenal;
	protected ref array<ref OYB_ArsenalEntry> m_aEntries;
	protected ref array<int> m_aFiltered = {};
	protected ref array<int> m_aCategoryCounts = {};

	protected int m_iCategory = OYB_EArsenalCategory.ALL;
	protected int m_iPage;
	protected string m_sSearch;
	protected float m_fArsenalCheckTimer;

	protected TextWidget m_wStatus;
	protected TextWidget m_wPageText;
	protected EditBoxWidget m_wSearch;
	protected ButtonWidget m_wPrevPage;
	protected ButtonWidget m_wNextPage;
	protected ref array<ref OYB_ArsenalRow> m_aCategoryButtons = {};
	protected ref array<ref OYB_ArsenalRow> m_aRows = {};
	protected ref array<TextWidget> m_aDetailLines = {};
	protected TextWidget m_wDetailTitle;

	static void SetPendingArsenal(SCR_ArsenalComponent arsenal)
	{
		s_PendingArsenal = arsenal;
	}

	override void OnMenuOpen()
	{
		super.OnMenuOpen();

		int started = System.GetTickCount();
		m_Arsenal = s_PendingArsenal;
		s_PendingArsenal = null;

		BuildWindow();

		InputManager input = GetGame().GetInputManager();
		input.AddActionListener(UIConstants.MENU_ACTION_BACK, EActionTrigger.PRESSED, OnBack);
		input.AddActionListener("MenuTabLeft", EActionTrigger.DOWN, PreviousCategory);
		input.AddActionListener("MenuTabRight", EActionTrigger.DOWN, NextCategory);

		if (!m_Arsenal)
		{
			Print("OYB_ARSENAL_UI opened without an arsenal, nothing to show", LogLevel.WARNING);
			m_wStatus.SetText("No arsenal found. Close this and use the action on an arsenal again.");
			Refresh();
			GetGame().GetWorkspace().SetFocusedWidget(m_aCategoryButtons[0].m_wButton);
			return;
		}

		bool fromCache;
		m_aEntries = OYB_ArsenalCatalogService.GetInstance().GetEntries(m_Arsenal, fromCache);
		CountCategories();
		UpdateStatus();
		Refresh();

		if (!m_aRows.IsEmpty() && m_aRows[0].m_iIndex >= 0)
			GetGame().GetWorkspace().SetFocusedWidget(m_aRows[0].m_wButton);
		else
			GetGame().GetWorkspace().SetFocusedWidget(m_aCategoryButtons[0].m_wButton);

		PrintFormat("OYB_ARSENAL_UI opened: %1 items, %2 ms, cached list: %3", m_aEntries.Count(), System.GetTickCount(started), fromCache);
	}

	override void OnMenuClose()
	{
		InputManager input = GetGame().GetInputManager();
		input.RemoveActionListener(UIConstants.MENU_ACTION_BACK, EActionTrigger.PRESSED, OnBack);
		input.RemoveActionListener("MenuTabLeft", EActionTrigger.DOWN, PreviousCategory);
		input.RemoveActionListener("MenuTabRight", EActionTrigger.DOWN, NextCategory);
		Print("OYB_ARSENAL_UI closed");

		super.OnMenuClose();
	}

	override void OnMenuUpdate(float tDelta)
	{
		super.OnMenuUpdate(tDelta);

		m_fArsenalCheckTimer += tDelta;
		if (m_fArsenalCheckTimer < ARSENAL_CHECK_INTERVAL)
			return;
		m_fArsenalCheckTimer = 0;

		if (m_aEntries != null && (!m_Arsenal || !m_Arsenal.IsArsenalEnabled()))
		{
			Print("OYB_ARSENAL_UI arsenal was removed or disabled while open, closing");
			Close();
		}
	}

	protected void OnBack()
	{
		// While typing, back leaves the search box instead of the menu.
		if (m_wSearch && m_wSearch.IsInWriteMode())
			return;

		if (IsFocused())
			Close();
	}

	//------------------------------------------------------------------------------------------------
	// Building the window

	protected void BuildWindow()
	{
		WorkspaceWidget workspace = GetGame().GetWorkspace();
		Widget root = GetRootWidget();

		Widget dim = workspace.CreateWidget(WidgetType.ImageWidgetTypeID, WidgetFlags.VISIBLE | WidgetFlags.BLEND | WidgetFlags.STRETCH | WidgetFlags.IGNORE_CURSOR, m_ColorDim, 0, root);
		Stretch(dim);

		Widget panel = workspace.CreateWidget(WidgetType.FrameWidgetTypeID, WidgetFlags.VISIBLE, null, 1, root);
		FrameSlot.SetAnchor(panel, 0.5, 0.5);
		FrameSlot.SetAlignment(panel, 0.5, 0.5);
		FrameSlot.SetPos(panel, 0, 0);
		FrameSlot.SetSize(panel, PANEL_WIDTH, PANEL_HEIGHT);

		Widget background = workspace.CreateWidget(WidgetType.ImageWidgetTypeID, WidgetFlags.VISIBLE | WidgetFlags.BLEND | WidgetFlags.STRETCH | WidgetFlags.IGNORE_CURSOR, m_ColorPanel, 0, panel);
		Stretch(background);

		TextWidget title = CreateText(panel, "OYB ARSENAL", 32, m_ColorAccent);
		Place(title, 32, 22, 800, 40);

		m_wStatus = CreateText(panel, "Reading arsenal...", 20, m_ColorMuted);
		Place(m_wStatus, 32, 66, 1536, 28);

		// Categories
		Widget categories = workspace.CreateWidget(WidgetType.VerticalLayoutWidgetTypeID, WidgetFlags.VISIBLE, null, 0, panel);
		Place(categories, 32, 116, 300, 680);
		for (int i = 0; i < OYB_ArsenalCategories.COUNT; i++)
		{
			m_aCategoryButtons.Insert(CreateRow(categories, OYB_ArsenalCategories.GetName(i)));
			m_aCategoryCounts.Insert(0);
		}

		// Search
		Widget searchBack = workspace.CreateWidget(WidgetType.ImageWidgetTypeID, WidgetFlags.VISIBLE | WidgetFlags.BLEND | WidgetFlags.STRETCH | WidgetFlags.IGNORE_CURSOR, m_ColorRow, 0, panel);
		Place(searchBack, 360, 116, 760, 44);
		m_wSearch = EditBoxWidget.Cast(workspace.CreateWidget(WidgetType.EditBoxWidgetTypeID, WidgetFlags.VISIBLE, m_ColorText, 1, panel));
		Place(m_wSearch, 372, 120, 736, 36);
		m_wSearch.SetPlaceholderText("Search items...");
		m_wSearch.SetVirtualKeyboardTitle("Search the arsenal");
		m_wSearch.AddHandler(this);

		// Item list: a fixed set of rows, refilled when the page changes.
		Widget list = workspace.CreateWidget(WidgetType.VerticalLayoutWidgetTypeID, WidgetFlags.VISIBLE, null, 0, panel);
		Place(list, 360, 172, 760, 620);
		for (int r = 0; r < PAGE_SIZE; r++)
			m_aRows.Insert(CreateRow(list, string.Empty));

		Widget paging = workspace.CreateWidget(WidgetType.HorizontalLayoutWidgetTypeID, WidgetFlags.VISIBLE, null, 0, panel);
		Place(paging, 360, 804, 760, 40);
		OYB_ArsenalRow previous = CreateRow(paging, "Previous page");
		OYB_ArsenalRow next = CreateRow(paging, "Next page");
		m_wPrevPage = previous.m_wButton;
		m_wNextPage = next.m_wButton;
		m_aCategoryButtons.Insert(previous);
		m_aCategoryButtons.Insert(next);
		m_wPageText = CreateText(paging, string.Empty, 20, m_ColorMuted);
		AlignableSlot.SetPadding(m_wPageText, 16, 8, 0, 0);

		// Item details
		Widget details = workspace.CreateWidget(WidgetType.VerticalLayoutWidgetTypeID, WidgetFlags.VISIBLE, null, 0, panel);
		Place(details, 1150, 116, 418, 680);
		m_wDetailTitle = CreateText(details, string.Empty, 26, m_ColorAccent);
		m_wDetailTitle.SetTextWrapping(true);
		for (int d = 0; d < 8; d++)
		{
			TextWidget line = CreateText(details, string.Empty, 19, m_ColorText);
			line.SetTextWrapping(true);
			AlignableSlot.SetPadding(line, 0, 6, 0, 0);
			m_aDetailLines.Insert(line);
		}

		TextWidget hint = CreateText(panel, "Back: Esc / B     Category: LB / RB or click     Nothing is equipped from this menu yet", 18, m_ColorMuted);
		Place(hint, 32, 846, 1536, 26);
	}

	protected OYB_ArsenalRow CreateRow(Widget parent, string label)
	{
		WorkspaceWidget workspace = GetGame().GetWorkspace();
		OYB_ArsenalRow row = new OYB_ArsenalRow();

		row.m_wButton = ButtonWidget.Cast(workspace.CreateWidget(WidgetType.ButtonWidgetTypeID, WidgetFlags.VISIBLE, null, 0, parent));
		LayoutSlot.SetHorizontalAlign(row.m_wButton, LayoutHorizontalAlign.Stretch);
		LayoutSlot.SetPadding(row.m_wButton, 0, 2, 4, 2);
		row.m_wButton.AddHandler(this);

		row.m_wBackground = ImageWidget.Cast(workspace.CreateWidget(WidgetType.ImageWidgetTypeID, WidgetFlags.VISIBLE | WidgetFlags.BLEND | WidgetFlags.STRETCH | WidgetFlags.IGNORE_CURSOR, m_ColorRow, 0, row.m_wButton));
		AlignableSlot.SetHorizontalAlign(row.m_wBackground, LayoutHorizontalAlign.Stretch);
		AlignableSlot.SetVerticalAlign(row.m_wBackground, LayoutVerticalAlign.Stretch);

		row.m_wText = CreateText(row.m_wButton, label, 20, m_ColorText);
		AlignableSlot.SetPadding(row.m_wText, 12, 5, 12, 5);
		return row;
	}

	protected TextWidget CreateText(Widget parent, string text, int size, Color color)
	{
		TextWidget widget = TextWidget.Cast(GetGame().GetWorkspace().CreateWidget(WidgetType.TextWidgetTypeID, WidgetFlags.VISIBLE | WidgetFlags.BLEND | WidgetFlags.IGNORE_CURSOR | WidgetFlags.NOFOCUS, color, 1, parent));
		widget.SetExactFontSize(size);
		widget.SetText(text);
		return widget;
	}

	protected void Place(Widget widget, float x, float y, float width, float height)
	{
		FrameSlot.SetPos(widget, x, y);
		FrameSlot.SetSize(widget, width, height);
	}

	protected void Stretch(Widget widget)
	{
		FrameSlot.SetAnchorMin(widget, 0, 0);
		FrameSlot.SetAnchorMax(widget, 1, 1);
		FrameSlot.SetOffsets(widget, 0, 0, 0, 0);
	}

	//------------------------------------------------------------------------------------------------
	// Filling it in

	protected void UpdateStatus()
	{
		string faction = "No faction";
		SCR_Faction arsenalFaction = m_Arsenal.GetAssignedFaction();
		if (arsenalFaction)
			faction = WidgetManager.Translate(arsenalFaction.GetFactionName());

		string state = "Arsenal ready";
		if (!m_Arsenal.IsArsenalEnabled())
			state = "Arsenal DISABLED";

		m_wStatus.SetText(string.Format("%1     %2     Saving: %3     Items offered: %4", faction, state, SaveTypeText(m_Arsenal.GetArsenalSaveType()), m_aEntries.Count()));
	}

	protected string SaveTypeText(SCR_EArsenalSaveType saveType)
	{
		switch (saveType)
		{
			case SCR_EArsenalSaveType.SAVING_DISABLED: return "off at this arsenal";
			case SCR_EArsenalSaveType.IN_ARSENAL_ITEMS_ONLY: return "only items from this arsenal";
			case SCR_EArsenalSaveType.FACTION_ITEMS_ONLY: return "only your faction's items";
			case SCR_EArsenalSaveType.FRIENDLY_AND_FACTION_ITEMS_ONLY: return "your faction's and friendly items";
			case SCR_EArsenalSaveType.NO_RESTRICTIONS: return "anything";
		}
		return typename.EnumToString(SCR_EArsenalSaveType, saveType);
	}

	protected void CountCategories()
	{
		for (int i = 0; i < OYB_ArsenalCategories.COUNT; i++)
			m_aCategoryCounts[i] = 0;

		foreach (OYB_ArsenalEntry entry : m_aEntries)
			m_aCategoryCounts[entry.m_iCategory] = m_aCategoryCounts[entry.m_iCategory] + 1;

		m_aCategoryCounts[OYB_EArsenalCategory.ALL] = m_aEntries.Count();

		for (int c = 0; c < OYB_ArsenalCategories.COUNT; c++)
			m_aCategoryButtons[c].m_wText.SetText(string.Format("%1  (%2)", OYB_ArsenalCategories.GetName(c), m_aCategoryCounts[c]));
	}

	protected void Refresh()
	{
		m_aFiltered.Clear();
		if (m_aEntries)
		{
			foreach (int index, OYB_ArsenalEntry entry : m_aEntries)
			{
				if (m_iCategory != OYB_EArsenalCategory.ALL && entry.m_iCategory != m_iCategory)
					continue;
				if (!m_sSearch.IsEmpty() && !entry.m_sSearch.Contains(m_sSearch))
					continue;
				m_aFiltered.Insert(index);
			}
		}

		int pages = Math.MaxInt(1, (m_aFiltered.Count() + PAGE_SIZE - 1) / PAGE_SIZE);
		m_iPage = Math.ClampInt(m_iPage, 0, pages - 1);

		for (int r = 0; r < PAGE_SIZE; r++)
		{
			OYB_ArsenalRow row = m_aRows[r];
			int filteredIndex = m_iPage * PAGE_SIZE + r;
			if (filteredIndex >= m_aFiltered.Count())
			{
				row.m_iIndex = -1;
				row.m_wButton.SetVisible(false);
				continue;
			}

			row.m_iIndex = m_aFiltered[filteredIndex];
			OYB_ArsenalEntry shown = m_aEntries[row.m_iIndex];
			string label = shown.m_sName;
			if (m_iCategory == OYB_EArsenalCategory.ALL)
				label = string.Format("%1   -  %2", shown.m_sName, OYB_ArsenalCategories.GetName(shown.m_iCategory));
			row.m_wText.SetText(label);
			row.m_wButton.SetVisible(true);
		}

		if (m_aFiltered.IsEmpty())
		{
			if (!m_aEntries)
				m_wPageText.SetText(string.Empty);
			else if (!m_sSearch.IsEmpty())
				m_wPageText.SetText("Nothing here matches the search");
			else
				m_wPageText.SetText("This arsenal offers nothing in this category");
		}
		else
		{
			m_wPageText.SetText(string.Format("Page %1 of %2   (%3 items)", m_iPage + 1, pages, m_aFiltered.Count()));
		}

		m_wPrevPage.SetEnabled(m_iPage > 0);
		m_wNextPage.SetEnabled(m_iPage < pages - 1);

		foreach (OYB_ArsenalRow button : m_aCategoryButtons)
			Highlight(button, false);
		foreach (OYB_ArsenalRow itemRow : m_aRows)
			Highlight(itemRow, false);

		// Refreshing can happen under the focused widget without it losing focus.
		OYB_ArsenalRow focused = FindRow(GetGame().GetWorkspace().GetFocusedWidget());
		if (focused)
			Highlight(focused, true);

		if (focused && m_aRows.Contains(focused))
			ShowDetails(focused.m_iIndex);
		else
			ShowDetails(-1);
	}

	protected void ShowDetails(int index)
	{
		foreach (TextWidget line : m_aDetailLines)
			line.SetText(string.Empty);

		if (index < 0 || !m_aEntries || index >= m_aEntries.Count())
		{
			m_wDetailTitle.SetText("Select an item to see its details");
			return;
		}

		OYB_ArsenalEntry entry = m_aEntries[index];
		m_wDetailTitle.SetText(entry.m_sName);

		string costType = typename.EnumToString(SCR_EArsenalSupplyCostType, m_Arsenal.GetSupplyCostType());
		m_aDetailLines[0].SetText("Category: " + OYB_ArsenalCategories.GetName(entry.m_iCategory));
		m_aDetailLines[1].SetText(string.Format("Supply cost: %1  (%2)", entry.m_iSupplyCost, costType));
		m_aDetailLines[2].SetText("Required rank: " + typename.EnumToString(SCR_ECharacterRank, entry.m_eRank));
		m_aDetailLines[3].SetText("Arsenal type: " + typename.EnumToString(SCR_EArsenalItemType, entry.m_eType));
		m_aDetailLines[4].SetText("Arsenal mode: " + typename.EnumToString(SCR_EArsenalItemMode, entry.m_eMode));
		m_aDetailLines[5].SetText("Weight, calibre and ammo type: not read yet");
		m_aDetailLines[6].SetText("Prefab: " + FilePath.StripPath(entry.m_sPrefab.GetPath()));
		if (!entry.m_bHasName)
			m_aDetailLines[7].SetText("This item has no display name in the game, so its file name is shown.");
	}

	protected void Highlight(OYB_ArsenalRow row, bool focused)
	{
		if (focused)
			row.m_wBackground.SetColor(m_ColorFocus);
		else if (m_aCategoryButtons.Find(row) == m_iCategory)
			row.m_wBackground.SetColor(m_ColorSelected);
		else
			row.m_wBackground.SetColor(m_ColorRow);
	}

	protected void SelectCategory(int category)
	{
		m_iCategory = category;
		m_iPage = 0;
		Refresh();
	}

	protected void PreviousCategory()
	{
		if (m_wSearch.IsInWriteMode())
			return;

		SelectCategory((m_iCategory + OYB_ArsenalCategories.COUNT - 1) % OYB_ArsenalCategories.COUNT);
		FocusFirstItem();
	}

	protected void NextCategory()
	{
		if (m_wSearch.IsInWriteMode())
			return;

		SelectCategory((m_iCategory + 1) % OYB_ArsenalCategories.COUNT);
		FocusFirstItem();
	}

	protected void FocusFirstItem()
	{
		if (m_aRows[0].m_iIndex >= 0)
			GetGame().GetWorkspace().SetFocusedWidget(m_aRows[0].m_wButton);
		else
			GetGame().GetWorkspace().SetFocusedWidget(m_aCategoryButtons[m_iCategory].m_wButton);
	}

	//------------------------------------------------------------------------------------------------
	// Widget events. Mouse hover moves the same focus the controller uses, so both
	// work off one selection.

	protected OYB_ArsenalRow FindRow(Widget w)
	{
		foreach (OYB_ArsenalRow row : m_aRows)
		{
			if (row.m_wButton == w)
				return row;
		}
		foreach (OYB_ArsenalRow button : m_aCategoryButtons)
		{
			if (button.m_wButton == w)
				return button;
		}
		return null;
	}

	override bool OnClick(Widget w, int x, int y, int button)
	{
		if (w == m_wPrevPage || w == m_wNextPage)
		{
			if (w == m_wPrevPage)
				m_iPage--;
			else
				m_iPage++;
			Refresh();
			GetGame().GetWorkspace().SetFocusedWidget(w);
			return true;
		}

		int category = -1;
		foreach (int i, OYB_ArsenalRow row : m_aCategoryButtons)
		{
			if (row.m_wButton == w)
				category = i;
		}
		if (category >= 0 && category < OYB_ArsenalCategories.COUNT)
		{
			SelectCategory(category);
			GetGame().GetWorkspace().SetFocusedWidget(w);
			return true;
		}

		OYB_ArsenalRow item = FindRow(w);
		if (item && item.m_iIndex >= 0)
		{
			ShowDetails(item.m_iIndex);
			return true;
		}

		return false;
	}

	override bool OnFocus(Widget w, int x, int y)
	{
		OYB_ArsenalRow row = FindRow(w);
		if (!row)
			return false;

		Highlight(row, true);
		if (row.m_iIndex >= 0 && m_aRows.Contains(row))
			ShowDetails(row.m_iIndex);
		return true;
	}

	override bool OnFocusLost(Widget w, int x, int y)
	{
		OYB_ArsenalRow row = FindRow(w);
		if (!row)
			return false;

		Highlight(row, false);
		return true;
	}

	override bool OnMouseEnter(Widget w, int x, int y)
	{
		if (!FindRow(w))
			return false;

		GetGame().GetWorkspace().SetFocusedWidget(w);
		return true;
	}

	override bool OnChange(Widget w, bool finished)
	{
		if (w != m_wSearch)
			return false;

		string search = m_wSearch.GetText();
		search.TrimInPlace();
		search.ToLower();
		if (search == m_sSearch)
			return true;

		m_sSearch = search;
		m_iPage = 0;
		Refresh();
		return true;
	}
}
