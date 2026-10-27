// Whenever an inventory panel shows an arsenal, OYB adds a bar above its item
// grid (the Category key, a < weapon > switcher and a search box), a category
// list to its left and saved loadouts under it. Arsenals are shown by the Vicinity panel (after
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
	protected static int s_iOYB_LoadoutSlot;
	// The page each category was last left on, so going back to one picks up
	// where you were.
	protected static ref map<int, int> s_mOYB_CategoryPages = new map<int, int>();

	protected Widget m_wOYB_FilterBar;
	protected Widget m_wOYB_CategoryColumn;
	protected Widget m_wOYB_LoadoutRow;
	protected TextWidget m_wOYB_WeaponText;
	protected SCR_InputButtonComponent m_OYB_WeaponKey;
	protected TextWidget m_wOYB_LoadoutText;
	protected SCR_EditBoxComponent m_OYB_Search;
	protected ref array<SCR_ButtonTextComponent> m_aOYB_CategoryButtons = {};
	protected ref array<TextWidget> m_aOYB_CategoryCountTexts = {};
	protected ref array<Widget> m_aOYB_CategoryAccents = {};
	protected ref array<int> m_aOYB_CategoryCounts = {};
	protected SCR_ArsenalComponent m_OYB_Arsenal;
	protected int m_iOYB_FitCount;
	protected bool m_bOYB_SkipLogged;
	protected IEntity m_OYB_FitWeapon;
	protected int m_iOYB_FitParts = -1;
	protected ref map<IEntity, bool> m_mOYB_Fits = new map<IEntity, bool>();
	protected string m_sOYB_WeaponName;

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

		// Before any filtering, while the list still holds one item per prefab.
		array<ResourceName> offered = {};
		listed.GetAvailablePrefabs(offered);
		OYB_ArsenalCatalogService.GetInstance().SortRounds(listed, pItemsInStorage, offered);

		array<bool> fits = {};
		m_iOYB_FitCount = OYB_FindFits(pItemsInStorage, fits);
		OYB_UpdateCategoryText();

		// A category this arsenal has nothing in (another box, or Fits after the
		// weapon was put away) would show an empty grid with its button hidden.
		if (s_iOYB_Category != OYB_EArsenalCategory.ALL && m_aOYB_CategoryCounts.IsIndexValid(s_iOYB_Category) && m_aOYB_CategoryCounts[s_iOYB_Category] == 0)
		{
			s_iOYB_Category = OYB_EArsenalCategory.ALL;
			OYB_UpdateCategoryText();
		}

		if (s_iOYB_Category == OYB_EArsenalCategory.ALL && s_sOYB_Search.IsEmpty())
			return;

		SCR_ArsenalComponent arsenal = m_OYB_Arsenal;

		// The panel's items are resolved one per prefab, in the order the arsenal
		// lists them, so the prefab for each item is found by position.
		array<ResourceName> prefabs = offered;
		if (prefabs.Count() != pItemsInStorage.Count())
		{
			if (!m_bOYB_SkipLogged)
				PrintFormat("OYB_ARSENAL filter skipped: %1 items shown but %2 offered", pItemsInStorage.Count(), prefabs.Count());
			m_bOYB_SkipLogged = true;
			return;
		}

		// Each word of the search has to appear somewhere in the name, in any
		// order, so "m16 ca" finds "M16A2 Carbine".
		array<string> words = {};
		s_sOYB_Search.Split(" ", words, true);

		OYB_ArsenalCatalogService catalog = OYB_ArsenalCatalogService.GetInstance();
		array<IEntity> kept = {};
		foreach (int i, IEntity item : pItemsInStorage)
		{
			OYB_ArsenalEntry entry = catalog.FindEntry(arsenal, prefabs[i]);
			if (!entry)
				continue;
			if (s_iOYB_Category == OYB_EArsenalCategory.FITS_WEAPON)
			{
				if (!fits[i])
					continue;
			}
			else if (s_iOYB_Category != OYB_EArsenalCategory.ALL && entry.m_iCategory != s_iOYB_Category)
			{
				continue;
			}
			if (!OYB_MatchesAll(entry.m_sSearch, words))
				continue;
			kept.Insert(item);
		}


		pItemsInStorage.Clear();
		pItemsInStorage.InsertAll(kept);
	}

	//------------------------------------------------------------------------------------------------
	// Weapon fit

	// Marks each item that fits the weapon in the player's hands, using the
	// game's own checks: an attachment slot that accepts it, or a muzzle whose
	// magazine well matches the magazine's (which covers underbarrel launchers).
	protected int OYB_FindFits(notnull array<IEntity> items, notnull array<bool> fits)
	{
		fits.Clear();

		BaseWeaponComponent weapon = OYB_GetTargetWeapon();
		array<AttachmentSlotComponent> slots = {};
		array<BaseMuzzleComponent> muzzles = {};
		IEntity weaponEntity;
		int fitted;
		if (weapon)
		{
			weaponEntity = weapon.GetOwner();
			weapon.GetAttachments(slots);
			weapon.GetMuzzlesList(muzzles);
			foreach (AttachmentSlotComponent slot : slots)
			{
				if (slot && slot.GetAttachedEntity())
					fitted++;
			}
		}

		// The answers only change with the weapon or what's fitted to it, so
		// they're kept between refreshes instead of being worked out for every
		// item each time the grid redraws.
		if (weaponEntity != m_OYB_FitWeapon || fitted != m_iOYB_FitParts)
		{
			m_OYB_FitWeapon = weaponEntity;
			m_iOYB_FitParts = fitted;
			m_mOYB_Fits.Clear();
			m_sOYB_WeaponName = string.Empty;
			if (weaponEntity && weaponEntity.GetPrefabData())
			{
				ResourceName weaponPrefab = weaponEntity.GetPrefabData().GetPrefabName();
				m_sOYB_WeaponName = OYB_ArsenalCatalogService.OYB_ReadInventoryName(weaponPrefab);
				if (m_sOYB_WeaponName.IsEmpty())
					m_sOYB_WeaponName = FilePath.StripExtension(FilePath.StripPath(weaponPrefab.GetPath()));
			}
		}

		int count;
		foreach (IEntity item : items)
		{
			bool fit;
			if (weapon && item && !m_mOYB_Fits.Find(item, fit))
			{
				fit = OYB_Fits(item, slots, muzzles);
				m_mOYB_Fits.Insert(item, fit);
			}
			fits.Insert(fit);
			if (fit)
				count++;
		}
		return count;
	}


	protected bool OYB_Fits(IEntity item, array<AttachmentSlotComponent> slots, array<BaseMuzzleComponent> muzzles)
	{
		MagazineComponent magazine = MagazineComponent.Cast(item.FindComponent(MagazineComponent));
		if (magazine && magazine.GetMagazineWell())
		{
			typename well = magazine.GetMagazineWell().Type();
			foreach (BaseMuzzleComponent muzzle : muzzles)
			{
				if (muzzle && muzzle.GetMagazineWell() && muzzle.GetMagazineWell().Type() == well)
					return true;
			}
			return false;
		}

		foreach (AttachmentSlotComponent slot : slots)
		{
			if (slot && slot.CanSetAttachment(item))
				return true;
		}
		return false;
	}

	// The weapon the fit list works on: the one in hands, or the one picked with
	// the Weapon button.
	protected BaseWeaponComponent OYB_GetTargetWeapon()
	{
		IEntity weaponEntity = OYB_TargetWeapon.Get(SCR_PlayerController.GetLocalControlledEntity());
		if (!weaponEntity)
			return null;

		return BaseWeaponComponent.Cast(weaponEntity.FindComponent(BaseWeaponComponent));
	}

	protected bool OYB_MatchesAll(string text, array<string> words)
	{
		foreach (string word : words)
		{
			if (!text.Contains(word))
				return false;
		}
		return true;
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

	override protected void FindAndSetTitleName(Widget targetTitle, BaseInventoryStorageComponent targetStorage)
	{
		super.FindAndSetTitleName(targetTitle, targetStorage);
		if (!OYB_GetArsenal(targetStorage))
			return;

		TextWidget titleNameWidget = TextWidget.Cast(targetTitle.FindAnyWidget("TextC"));
		if (titleNameWidget)
			titleNameWidget.SetText("OYB Arsenal");
	}

	void OYB_SetCategory(int category)
	{
		if (category == s_iOYB_Category)
			return;

		s_mOYB_CategoryPages.Set(s_iOYB_Category, m_iLastShownPage);
		s_iOYB_Category = category;
		m_iLastShownPage = s_mOYB_CategoryPages.Get(category);
		Refresh();
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
		s_mOYB_CategoryPages.Clear();
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
		if (m_wOYB_CategoryColumn)
			m_wOYB_CategoryColumn.SetVisible(show);
		if (m_wOYB_LoadoutRow)
			m_wOYB_LoadoutRow.SetVisible(show);
	}

	protected void OYB_BuildFilterBar()
	{
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

		if (m_wStorageName && Type() == SCR_InventoryOpenedStorageArsenalUI)
			m_wStorageName.SetText("OYB Arsenal");

		Widget categoryRow = workspace.CreateWidget(WidgetType.HorizontalLayoutWidgetTypeID, WidgetFlags.VISIBLE, null, 0, bar);
		AlignableSlot.SetHorizontalAlign(categoryRow, LayoutHorizontalAlign.Stretch);

		// The inventory already uses MenuTabLeft / MenuTabRight (Q / E, LB / RB) to
		// turn the grid's pages, so categories cycle on MenuFilter instead.
		SCR_InputButtonComponent cycle = OYB_CreateNavButton(categoryRow, "MenuFilter", "Category");
		if (cycle)
			cycle.m_OnActivated.Insert(OYB_OnNextCategory);

		// The Weapon key and < M16A2 > pick which carried weapon the Fits list
		// and attachment fitting work on. On a controller MenuRefresh is the same
		// button as Requisition, so there the key is hidden and the weapon in
		// hand is used.
		m_OYB_WeaponKey = OYB_CreateNavButton(categoryRow, "MenuRefresh", "Weapon");
		if (m_OYB_WeaponKey)
			m_OYB_WeaponKey.m_OnActivated.Insert(OYB_OnWeaponKey);

		SCR_ButtonTextComponent previousWeapon, nextWeapon;
		m_wOYB_WeaponText = OYB_BuildSwitcher(categoryRow, previousWeapon, nextWeapon);
		if (previousWeapon)
			previousWeapon.m_OnClicked.Insert(OYB_OnPreviousWeapon);
		if (nextWeapon)
			nextWeapon.m_OnClicked.Insert(OYB_OnNextWeapon);

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
		OYB_BuildLoadoutRow();
	}

	// Under the item grid: a "Saved loadouts" heading, then < slot > to pick a
	// slot, Save to store the current kit in it and Load to put it on here. The
	// server keeps the slots and charges for what's missing.
	protected void OYB_BuildLoadoutRow()
	{
		Widget gridRow = m_wGrid.GetParent();
		if (!gridRow || !gridRow.GetParent())
			return;

		WorkspaceWidget workspace = GetGame().GetWorkspace();
		Widget area = workspace.CreateWidget(WidgetType.VerticalLayoutWidgetTypeID, WidgetFlags.VISIBLE, null, 0, gridRow.GetParent());
		if (!area)
			return;
		area.SetName("OYB_LoadoutRow");
		m_wOYB_LoadoutRow = area;
		AlignableSlot.SetHorizontalAlign(area, LayoutHorizontalAlign.Stretch);
		AlignableSlot.SetPadding(area, 0, 8, 0, 0);
		Widget afterGrid = gridRow.GetSibling();
		if (afterGrid && afterGrid != area)
			OYB_MoveBefore(area, afterGrid);

		Widget heading = workspace.CreateWidgets(OYB_LABEL_LAYOUT, area);
		if (heading)
		{
			TextWidget headingText = TextWidget.Cast(OYB_FindWidgetOfType(heading, TextWidget));
			if (headingText)
			{
				headingText.SetText("SAVED LOADOUTS");
				headingText.SetExactFontSize(14);
				headingText.SetColor(new Color(0.55, 0.55, 0.55, 1));
			}
		}

		Widget row = workspace.CreateWidget(WidgetType.HorizontalLayoutWidgetTypeID, WidgetFlags.VISIBLE, null, 0, area);
		if (!row)
			return;
		AlignableSlot.SetHorizontalAlign(row, LayoutHorizontalAlign.Stretch);
		AlignableSlot.SetPadding(row, 0, 2, 0, 0);

		SCR_ButtonTextComponent previousSlot, nextSlot;
		m_wOYB_LoadoutText = OYB_BuildSwitcher(row, previousSlot, nextSlot);
		if (previousSlot)
			previousSlot.m_OnClicked.Insert(OYB_OnPreviousLoadoutSlot);
		if (nextSlot)
			nextSlot.m_OnClicked.Insert(OYB_OnNextLoadoutSlot);

		// Key buttons, so they work from a keyboard or controller with their
		// prompt shown, and can still be clicked.
		// MenuSave shares X with Category inside the inventory.
		SCR_InputButtonComponent save = OYB_CreateNavButton(row, "MenuFavourite", "Save");
		if (save)
			save.m_OnActivated.Insert(OYB_OnSaveKey);

		SCR_InputButtonComponent load = OYB_CreateNavButton(row, OYB_LOAD_ACTION, "Load");
		if (load)
			load.m_OnActivated.Insert(OYB_OnLoadKey);

		Widget credits = workspace.CreateWidgets(OYB_LABEL_LAYOUT, area);
		if (credits)
		{
			AlignableSlot.SetPadding(credits, 0, 6, 0, 0);
			TextWidget creditsText = TextWidget.Cast(OYB_FindWidgetOfType(credits, TextWidget));
			if (creditsText)
			{
				creditsText.SetText("Zero fucking thanks to Tay and Saber");
				creditsText.SetExactFontSize(11);
				creditsText.SetColor(new Color(0.45, 0.45, 0.45, 1));
			}
		}

		SCR_PlayerController.OYB_GetOnLoadoutNames().Insert(OYB_UpdateLoadoutText);
		OYB_UpdateLoadoutText();

		SCR_PlayerController controller = SCR_PlayerController.Cast(GetGame().GetPlayerController());
		if (controller)
		{
			controller.OYB_RequestLoadoutNames();
			controller.OYB_RequestSettings();
		}
	}

	override event void HandlerDeattached(Widget w)
	{
		SCR_PlayerController.OYB_GetOnLoadoutNames().Remove(OYB_UpdateLoadoutText);
		super.HandlerDeattached(w);
	}

	// V on keyboard (the game's MenuDelete, a click action). Space (MenuDeploy)
	// only works on the respawn screen and MenuRename is R, the Weapon key.
	protected static const string OYB_LOAD_ACTION = "MenuDelete";

	protected void OYB_UpdateLoadoutText()
	{
		if (!m_wOYB_LoadoutText)
			return;

		string text = string.Format("Loadout %1", s_iOYB_LoadoutSlot + 1);
		array<string> names = SCR_PlayerController.OYB_GetLoadoutNames();
		if (!names.IsIndexValid(s_iOYB_LoadoutSlot) || names[s_iOYB_LoadoutSlot].IsEmpty())
			text += " (empty)";

		m_wOYB_LoadoutText.SetText(text);
	}

	protected void OYB_OnNextLoadoutSlot(SCR_ButtonBaseComponent button)
	{
		s_iOYB_LoadoutSlot = (s_iOYB_LoadoutSlot + 1) % SCR_PlayerController.OYB_LOADOUT_SLOTS;
		OYB_UpdateLoadoutText();
	}

	protected void OYB_OnPreviousLoadoutSlot(SCR_ButtonBaseComponent button)
	{
		int slots = SCR_PlayerController.OYB_LOADOUT_SLOTS;
		s_iOYB_LoadoutSlot = (s_iOYB_LoadoutSlot + slots - 1) % slots;
		OYB_UpdateLoadoutText();
	}

	protected void OYB_OnSaveKey(SCR_InputButtonComponent button, string action)
	{
		OYB_OnSaveLoadout(null);
	}

	protected void OYB_OnLoadKey(SCR_InputButtonComponent button, string action)
	{
		OYB_OnLoadLoadout(null);
	}

	protected void OYB_OnSaveLoadout(SCR_ButtonBaseComponent button)
	{
		SCR_PlayerController controller = SCR_PlayerController.Cast(GetGame().GetPlayerController());
		if (!controller)
			return;

		// The arsenal goes along so the saved kit can also become the respawn kit
		// under that arsenal's own save rules.
		RplId arsenalResourceId = RplId.Invalid();
		if (m_OYB_Arsenal)
		{
			SCR_ResourceComponent resourceComponent = SCR_ResourceComponent.FindResourceComponent(m_OYB_Arsenal.GetOwner());
			if (resourceComponent)
				arsenalResourceId = Replication.FindItemId(resourceComponent);
		}

		controller.OYB_SaveLoadout(s_iOYB_LoadoutSlot, arsenalResourceId);
	}

	protected void OYB_OnLoadLoadout(SCR_ButtonBaseComponent button)
	{
		SCR_PlayerController controller = SCR_PlayerController.Cast(GetGame().GetPlayerController());
		if (!controller || !m_OYB_Arsenal)
			return;

		SCR_ResourceComponent resourceComponent = SCR_ResourceComponent.FindResourceComponent(m_OYB_Arsenal.GetOwner());
		if (!resourceComponent)
		{
			Print("OYB_ARSENAL this arsenal has no supply component, can't load a kit from it", LogLevel.WARNING);
			return;
		}

		controller.OYB_LoadLoadout(s_iOYB_LoadoutSlot, Replication.FindItemId(resourceComponent));
	}

	// "<  text  >": arrow buttons either side of a centred label that fills the
	// rest of the row.
	protected TextWidget OYB_BuildSwitcher(Widget parent, out SCR_ButtonTextComponent previous, out SCR_ButtonTextComponent next)
	{
		WorkspaceWidget workspace = GetGame().GetWorkspace();
		Widget box = workspace.CreateWidget(WidgetType.HorizontalLayoutWidgetTypeID, WidgetFlags.VISIBLE, null, 0, parent);
		if (!box)
			return null;
		LayoutSlot.SetSizeMode(box, LayoutSizeMode.Fill);
		AlignableSlot.SetVerticalAlign(box, LayoutVerticalAlign.Center);
		AlignableSlot.SetPadding(box, 4, 0, 6, 0);

		// Buttons and labels made from layout files bring their own z-order, which
		// is what a layout sorts by, so the order is set by hand.
		previous = OYB_CreateTextButton(box, "<");
		if (previous)
		{
			OYB_StyleArrowButton(previous);
			previous.GetRootWidget().SetZOrder(0);
		}

		TextWidget text;
		Widget label = workspace.CreateWidgets(OYB_LABEL_LAYOUT, box);
		if (label)
		{
			text = TextWidget.Cast(OYB_FindWidgetOfType(label, TextWidget));
			LayoutSlot.SetSizeMode(label, LayoutSizeMode.Fill);
			AlignableSlot.SetHorizontalAlign(label, LayoutHorizontalAlign.Center);
			AlignableSlot.SetVerticalAlign(label, LayoutVerticalAlign.Center);
			label.SetZOrder(1);
		}

		next = OYB_CreateTextButton(box, ">");
		if (next)
		{
			OYB_StyleArrowButton(next);
			next.GetRootWidget().SetZOrder(2);
		}

		return text;
	}

	protected static ref Color OYB_ORANGE = new Color(0.898, 0.541, 0.184, 1);

	// Category list rows: dark, light grey text, a lighter row and white text for
	// the one selected.
	protected void OYB_StyleListButton(SCR_ButtonTextComponent button)
	{
		button.SetBackgroundColors(new Color(1, 1, 1, 0.02), new Color(1, 1, 1, 0.1), new Color(1, 1, 1, 0.14), new Color(1, 1, 1, 0.2), new Color(0.898, 0.541, 0.184, 0.5));
		button.m_bUseTextColorization = true;
		button.m_TextDefault = new Color(0.7, 0.7, 0.7, 1);
		button.m_TextToggled = new Color(1, 1, 1, 1);
		button.ColorizeBackground(false);
	}

	protected void OYB_StyleArrowButton(SCR_ButtonTextComponent button)
	{
		button.SetBackgroundColors(new Color(1, 1, 1, 0.06), new Color(1, 1, 1, 0.16), new Color(1, 1, 1, 0.06), new Color(1, 1, 1, 0.16), new Color(1, 1, 1, 0.3));
		button.m_bUseTextColorization = true;
		button.m_TextToggled = OYB_ORANGE;
		button.m_TextDefault = OYB_ORANGE;
		button.ColorizeBackground(false);
		if (button.GetTextWidget())
			button.GetTextWidget().SetColor(OYB_ORANGE);
	}

	protected SCR_ButtonTextComponent OYB_CreateTextButton(Widget parent, string text)
	{
		Widget root = GetGame().GetWorkspace().CreateWidgets(OYB_TAB_BUTTON_LAYOUT, parent);
		if (!root)
			return null;

		AlignableSlot.SetVerticalAlign(root, LayoutVerticalAlign.Center);
		AlignableSlot.SetPadding(root, 0, 0, 6, 0);
		SCR_ButtonTextComponent button = SCR_ButtonTextComponent.Cast(root.FindHandler(SCR_ButtonTextComponent));
		if (button)
			button.SetText(text);
		return button;
	}

	// A column of category buttons to the left of the item grid. The grid is
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
		Widget column = workspace.CreateWidget(WidgetType.VerticalLayoutWidgetTypeID, WidgetFlags.VISIBLE, null, 0, row);
		if (!column)
			return;
		column.SetName("OYB_CategoryColumn");
		m_wOYB_CategoryColumn = column;
		AlignableSlot.SetPadding(column, 0, 0, 8, 0);
		AlignableSlot.SetVerticalAlign(column, LayoutVerticalAlign.Top);

		// Added after the column, so it sits to its right. A widget with nothing
		// holding it is deleted when it's taken out of its parent; the member is a
		// weak reference, so a local keeps it alive.
		Widget grid = m_wGrid;
		container.RemoveChild(grid);
		row.AddChild(grid);
		OYB_KeepCreationOrder(row);

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

			TextWidget countText;
			Widget accent;
			if (button)
			{
				button.SetText(OYB_ArsenalCategories.GetName(category));
				button.SetToggleable(true);
				button.m_OnClicked.Insert(OYB_OnCategoryClicked);
				OYB_StyleListButton(button);
				OYB_AddCountAndAccent(button, countText, accent);
			}
			m_aOYB_CategoryButtons.Insert(button);
			m_aOYB_CategoryCountTexts.Insert(countText);
			m_aOYB_CategoryAccents.Insert(accent);
		}
	}

	// Puts the name on the left, the count on the right and a thin orange edge
	// for the selected row. Only possible when the button's text sits in an
	// overlay; otherwise the count goes after the name.
	protected void OYB_AddCountAndAccent(SCR_ButtonTextComponent button, out TextWidget countText, out Widget accent)
	{
		TextWidget name = button.GetTextWidget();
		if (!name || !name.GetParent() || name.GetParent().GetTypeID() != WidgetType.OverlayWidgetTypeID)
			return;

		Widget overlay = name.GetParent();
		AlignableSlot.SetHorizontalAlign(name, LayoutHorizontalAlign.Left);
		AlignableSlot.SetPadding(name, 10, 0, 34, 0);

		WorkspaceWidget workspace = GetGame().GetWorkspace();
		Widget label = workspace.CreateWidgets(OYB_LABEL_LAYOUT, overlay);
		if (label)
		{
			AlignableSlot.SetHorizontalAlign(label, LayoutHorizontalAlign.Right);
			AlignableSlot.SetVerticalAlign(label, LayoutVerticalAlign.Center);
			AlignableSlot.SetPadding(label, 0, 0, 8, 0);
			countText = TextWidget.Cast(OYB_FindWidgetOfType(label, TextWidget));
		}

		ImageWidget edge = ImageWidget.Cast(workspace.CreateWidget(WidgetType.ImageWidgetTypeID, WidgetFlags.VISIBLE, OYB_ORANGE, 0, overlay));
		if (edge)
		{
			edge.SetSize(3, 1);
			AlignableSlot.SetHorizontalAlign(edge, LayoutHorizontalAlign.Left);
			AlignableSlot.SetVerticalAlign(edge, LayoutVerticalAlign.Stretch);
			accent = edge;
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
		counts[OYB_EArsenalCategory.FITS_WEAPON] = m_iOYB_FitCount;

		if (m_OYB_WeaponKey)
			m_OYB_WeaponKey.GetRootWidget().SetVisible(GetGame().GetInputManager().IsUsingMouseAndKeyboard());

		if (m_wOYB_WeaponText)
		{
			string weaponLabel = "No weapon";
			if (!m_sOYB_WeaponName.IsEmpty())
				weaponLabel = m_sOYB_WeaponName;
			m_wOYB_WeaponText.SetText(weaponLabel);
		}

		// Categories this arsenal has nothing in get no button.
		foreach (int category, SCR_ButtonTextComponent button : m_aOYB_CategoryButtons)
		{
			if (!button)
				continue;

			bool hasItems = category == OYB_EArsenalCategory.ALL || counts[category] > 0;
			bool selected = category == s_iOYB_Category;
			button.GetRootWidget().SetVisible(hasItems);
			button.SetToggled(selected, false, false, true);

			TextWidget countText = m_aOYB_CategoryCountTexts[category];
			if (countText)
			{
				button.SetText(OYB_CategoryName(category));
				countText.SetText(counts[category].ToString());
				if (selected)
					countText.SetColor(OYB_ORANGE);
				else
					countText.SetColor(new Color(0.5, 0.5, 0.5, 1));
			}
			else
			{
				button.SetText(string.Format("%1 (%2)", OYB_CategoryName(category), counts[category]));
			}

			Widget accent = m_aOYB_CategoryAccents[category];
			if (accent)
				accent.SetVisible(selected);
		}
	}

	protected void OYB_OnNextWeapon(SCR_ButtonBaseComponent button)
	{
		OYB_TargetWeapon.Next(SCR_PlayerController.GetLocalControlledEntity(), 1);
		OYB_ShowFits();
	}

	protected void OYB_OnWeaponKey(SCR_InputButtonComponent button, string action)
	{
		if (!GetGame().GetInputManager().IsUsingMouseAndKeyboard())
			return;

		OYB_OnNextWeapon(null);
	}

	protected void OYB_OnPreviousWeapon(SCR_ButtonBaseComponent button)
	{
		OYB_TargetWeapon.Next(SCR_PlayerController.GetLocalControlledEntity(), -1);
		OYB_ShowFits();
	}

	protected void OYB_ShowFits()
	{
		s_iOYB_Category = OYB_EArsenalCategory.FITS_WEAPON;
		OYB_ApplyFilters();
	}

	// "Fits weapon" names the weapon the fit list is for.
	protected string OYB_CategoryName(int category)
	{
		if (category == OYB_EArsenalCategory.FITS_WEAPON && !m_sOYB_WeaponName.IsEmpty())
			return "Fits " + m_sOYB_WeaponName;

		return OYB_ArsenalCategories.GetName(category);
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
