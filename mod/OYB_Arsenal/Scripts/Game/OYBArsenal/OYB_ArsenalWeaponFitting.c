// Double-clicking an attachment in the arsenal (or dragging it onto the character)
// fits it straight onto the chosen weapon: the one in the player's hands, or the
// one picked with the arsenal's Weapon button. If that slot already has
// something in it, say a muzzle brake when fitting a suppressor, the old part goes
// back into the arsenal with the game's own refund, and the new one is requested
// only once the slot is seen to be empty. The server takes the supplies before it
// spawns the item, so asking while the slot is still full would cost supplies for
// nothing; if the refund is refused, nothing new is requested.
//
// The new part is asked for with the same arsenal request the game uses when an
// item is dragged onto a weapon, so the server still checks the storage belongs to
// the player, the distance, the arsenal and the supply cost. Accessories go onto
// worn gear the same way. Everything else keeps the vanilla behaviour.
modded class SCR_InventoryMenuUI
{
	// The game decides whether Q / E turn pages when the mouse enters a panel.
	// Switching category changes the page count without leaving the panel, so
	// it's checked again every frame for the panel under the mouse.
	override void OnMenuUpdate(float tDelta)
	{
		if (m_pActiveHoveredStorageUI)
			m_bEnablePagingContext = m_pActiveHoveredStorageUI.GetPageCount() > 1;

		super.OnMenuUpdate(tDelta);
	}

	override protected bool MoveBetweenFromVicinity_VirtualArsenal()
	{
		if (OYB_FitToHeldWeapon("quick move") || OYB_EquipClothing("quick move") || OYB_EquipAccessory("quick move") || OYB_EquipWeapon("quick move") || OYB_PutWithSameItems("quick move"))
			return true;

		return super.MoveBetweenFromVicinity_VirtualArsenal();
	}

	override protected bool EquipDraggedItem_VirtualArsenal(bool forceEquip = false)
	{
		if (OYB_FitToHeldWeapon("drag onto character") || OYB_EquipClothing("drag onto character") || OYB_EquipAccessory("drag onto character") || OYB_EquipWeapon("drag onto character") || OYB_PutWithSameItems("drag onto character"))
			return true;

		return super.EquipDraggedItem_VirtualArsenal(forceEquip);
	}

	// The equip action works on the focused slot rather than a selected one.
	override protected void Action_EquipItem()
	{
		if (SCR_ArsenalInventorySlotUI.Cast(m_pFocusedSlotUI))
		{
			SCR_InventorySlotUI selected = m_pSelectedSlotUI;
			m_pSelectedSlotUI = m_pFocusedSlotUI;
			bool fitted = OYB_FitToHeldWeapon("equip") || OYB_EquipClothing("equip") || OYB_EquipAccessory("equip") || OYB_EquipWeapon("equip");
			m_pSelectedSlotUI = selected;
			if (fitted)
			{
				NavigationBarUpdate();
				return;
			}
		}

		super.Action_EquipItem();
	}

	protected bool OYB_FitToHeldWeapon(string via)
	{
		SCR_ArsenalInventorySlotUI arsenalSlot = SCR_ArsenalInventorySlotUI.Cast(m_pSelectedSlotUI);
		if (!arsenalSlot || !arsenalSlot.IsAvailable() || !arsenalSlot.GetInventoryItemComponent())
			return false;

		IEntity item = arsenalSlot.GetInventoryItemComponent().GetOwner();
		if (!item || item.FindComponent(BaseMagazineComponent))
			return false;

		IEntity weaponEntity = OYB_TargetWeapon.Get(m_Player);
		if (!weaponEntity)
			return false;

		BaseWeaponComponent weapon = BaseWeaponComponent.Cast(weaponEntity.FindComponent(BaseWeaponComponent));
		if (!weapon || !weaponEntity.FindComponent(WeaponAttachmentsStorageComponent))
		{
			PrintFormat("OYB_ARSENAL fit (%1): the held weapon has no attachment storage", via);
			return false;
		}

		// Slots on fitted parts too, so a scope can go on a rail adapter.
		array<AttachmentSlotComponent> slots = {};
		array<IEntity> owners = {};
		OYB_TargetWeapon.GetSlots(weaponEntity, slots, owners);
		AttachmentSlotComponent target;
		BaseInventoryStorageComponent targetStorage;
		foreach (int index, AttachmentSlotComponent slot : slots)
		{
			if (slot && slot.CanSetAttachment(item))
			{
				targetStorage = OYB_TargetWeapon.GetSlotStorage(owners[index]);
				if (!targetStorage)
					continue;

				target = slot;
				break;
			}
		}
		if (!target)
			return false;

		IEntity arsenalEntity = SCR_InventoryStorageBaseUI.ARSENAL_SLOT_STORAGES.Get(arsenalSlot);
		SCR_ResourceComponent resourceComponent = SCR_ResourceComponent.FindResourceComponent(arsenalEntity);
		if (!resourceComponent)
			return false;

		RplId resourceId = Replication.FindItemId(resourceComponent);
		RplId storageId = Replication.FindItemId(targetStorage);
		ResourceName resource = arsenalSlot.GetItemResource();

		IEntity fitted = target.GetAttachedEntity();
		if (!fitted)
		{
			PrintFormat("OYB_ARSENAL fit (%1): slot empty, requesting %2", via, resource.GetPath());
			OYB_ArsenalRequest.Request(resourceId, storageId, resource);
			return true;
		}

		PrintFormat("OYB_ARSENAL fit (%1): returning the fitted part to the arsenal, then requesting %2", via, resource.GetPath());

		// The same checks the server makes before it takes a part back.
		InventoryItemComponent fittedItem = InventoryItemComponent.Cast(fitted.FindComponent(InventoryItemComponent));
		SCR_ItemAttributeCollection fittedAttributes;
		if (fittedItem)
			fittedAttributes = SCR_ItemAttributeCollection.Cast(fittedItem.GetAttributes());
		SCR_ArsenalComponent arsenal = SCR_ArsenalComponent.FindArsenalComponent(arsenalEntity);
		if (!fittedAttributes || !fittedAttributes.IsRefundable() || !arsenal || !arsenal.IsRefundEnabled())
		{
			Print("OYB_ARSENAL the fitted part can't go back into this arsenal, nothing changed");
			SCR_UISoundEntity.SoundEvent(SCR_SoundEvent.SOUND_INV_DROP_ERROR);
			return true;
		}

		SCR_ResourcePlayerControllerInventoryComponent requests = SCR_ResourcePlayerControllerInventoryComponent.Cast(GetGame().GetPlayerController().FindComponent(SCR_ResourcePlayerControllerInventoryComponent));
		if (!requests)
			return true;

		requests.RpcAsk_ArsenalRefundItem(resourceId, Replication.FindItemId(fittedItem), EResourceType.SUPPLIES);
		OYB_PendingFit.Start(target, resourceId, storageId, resource);
		return true;
	}

	// An item taken from the arsenal goes into the pouch or pocket that already
	// holds the most of the same item, so a new magazine joins the others rather
	// than landing in whichever storage the game finds first. If none of those
	// has room, the game's own choice applies.
	protected bool OYB_PutWithSameItems(string via)
	{
		SCR_ArsenalInventorySlotUI arsenalSlot = SCR_ArsenalInventorySlotUI.Cast(m_pSelectedSlotUI);
		if (!arsenalSlot || !arsenalSlot.IsAvailable() || !arsenalSlot.GetInventoryItemComponent() || !m_InventoryManager)
			return false;

		IEntity preview = arsenalSlot.GetInventoryItemComponent().GetOwner();
		ResourceName resource = arsenalSlot.GetItemResource();
		if (!preview || resource.IsEmpty())
			return false;

		map<BaseInventoryStorageComponent, int> holding = new map<BaseInventoryStorageComponent, int>();
		array<IEntity> items = {};
		m_InventoryManager.GetItems(items);
		foreach (IEntity item : items)
		{
			if (!item || !item.GetPrefabData() || item.GetPrefabData().GetPrefabName() != resource)
				continue;

			InventoryItemComponent itemComponent = InventoryItemComponent.Cast(item.FindComponent(InventoryItemComponent));
			if (!itemComponent || !itemComponent.GetParentSlot())
				continue;

			// Only pouches, pockets and packs; not a weapon's magazine well.
			UniversalInventoryStorageComponent storage = UniversalInventoryStorageComponent.Cast(itemComponent.GetParentSlot().GetStorage());
			if (storage)
				holding.Set(storage, holding.Get(storage) + 1);
		}

		BaseInventoryStorageComponent best;
		int bestCount;
		foreach (BaseInventoryStorageComponent storage, int count : holding)
		{
			if (count > bestCount && m_InventoryManager.CanInsertItemInStorage(preview, storage))
			{
				best = storage;
				bestCount = count;
			}
		}
		if (!best)
			return false;

		SCR_ResourceComponent resourceComponent = SCR_ResourceComponent.FindResourceComponent(SCR_InventoryStorageBaseUI.ARSENAL_SLOT_STORAGES.Get(arsenalSlot));
		if (!resourceComponent)
			return false;

		string holder = "storage";
		if (best.GetOwner().GetPrefabData())
			holder = FilePath.StripPath(best.GetOwner().GetPrefabData().GetPrefabName().GetPath());
		PrintFormat("OYB_ARSENAL take (%1): %2 goes in with the %3 already in %4", via, FilePath.StripPath(resource.GetPath()), bestCount, holder);
		OYB_ArsenalRequest.Request(Replication.FindItemId(resourceComponent), Replication.FindItemId(best), resource);
		return true;
	}

	// A weapon goes into a free weapon slot that takes it. With none free, the
	// weapon in the slot it would go in is taken off and goes back into the
	// arsenal, scope, magazine and all, once the new one is on. See
	// OYB_ArsenalClothing.c.
	protected bool OYB_EquipWeapon(string via)
	{
		SCR_ArsenalInventorySlotUI arsenalSlot = SCR_ArsenalInventorySlotUI.Cast(m_pSelectedSlotUI);
		if (!arsenalSlot || !arsenalSlot.IsAvailable() || !arsenalSlot.GetInventoryItemComponent() || !m_InventoryManager || !m_pWeaponStorageComp)
			return false;

		IEntity preview = arsenalSlot.GetInventoryItemComponent().GetOwner();
		if (!preview)
			return false;

		// Grenades are weapons too, but they belong in pouches.
		BaseWeaponComponent weapon = BaseWeaponComponent.Cast(preview.FindComponent(BaseWeaponComponent));
		if (!weapon)
			return false;
		EWeaponType type = weapon.GetWeaponType();
		if (type == EWeaponType.WT_NONE || type == EWeaponType.WT_FRAGGRENADE || type == EWeaponType.WT_SMOKEGRENADE)
			return false;

		IEntity arsenalEntity = SCR_InventoryStorageBaseUI.ARSENAL_SLOT_STORAGES.Get(arsenalSlot);
		SCR_ResourceComponent resourceComponent = SCR_ResourceComponent.FindResourceComponent(arsenalEntity);
		if (!resourceComponent)
			return false;

		RplId resourceId = Replication.FindItemId(resourceComponent);
		ResourceName resource = arsenalSlot.GetItemResource();

		if (m_InventoryManager.CanInsertItemInStorage(preview, m_pWeaponStorageComp))
		{
			PrintFormat("OYB_ARSENAL weapon (%1): free slot, requesting %2", via, resource.GetPath());
			OYB_ArsenalRequest.Request(resourceId, Replication.FindItemId(m_pWeaponStorageComp), resource);
			return true;
		}

		InventoryStorageSlot slot = OYB_FindWeaponToReplace(preview, type);
		if (!slot)
		{
			Print("OYB_ARSENAL weapon: no carried weapon can make way for this one, using the game's own move");
			return false;
		}

		IEntity old = slot.GetAttachedEntity();
		PrintFormat("OYB_ARSENAL weapon (%1): taking off %2 to equip %3", via, FilePath.StripPath(old.GetPrefabData().GetPrefabName().GetPath()), resource.GetPath());
		if (!m_InventoryManager.TryRemoveItemFromStorage(old, slot.GetStorage()))
		{
			Print("OYB_ARSENAL the weapon can't be taken off, nothing changed");
			SCR_UISoundEntity.SoundEvent(SCR_SoundEvent.SOUND_INV_DROP_ERROR);
			return true;
		}

		OYB_PendingSwap.Start(m_InventoryManager, m_pWeaponStorageComp, slot, old, resourceId, resource, SCR_ArsenalComponent.FindArsenalComponent(arsenalEntity), false);
		return true;
	}

	// The weapon slot to swap: the one with the weapon picked in the arsenal's
	// bar (the gun in hand unless another was picked) if the new weapon can take
	// its place, otherwise the first carried gun it can replace. A slot
	// counts if the inventory says the new weapon can replace what's there, or
	// if what's there is the same kind of weapon (rifle for rifle, pistol for
	// pistol), since weapon slots are set up by kind.
	protected InventoryStorageSlot OYB_FindWeaponToReplace(IEntity preview, EWeaponType type)
	{
		IEntity chosen = OYB_TargetWeapon.Get(m_Player);
		InventoryStorageSlot fallback;
		for (int i = 0; i < m_pWeaponStorageComp.GetSlotsCount(); i++)
		{
			InventoryStorageSlot slot = m_pWeaponStorageComp.GetSlot(i);
			if (!slot || !slot.GetAttachedEntity())
				continue;

			BaseWeaponComponent current = BaseWeaponComponent.Cast(slot.GetAttachedEntity().FindComponent(BaseWeaponComponent));
			bool sameKind = current && current.GetWeaponType() == type;
			if (!sameKind && !m_InventoryManager.CanReplaceItem(preview, m_pWeaponStorageComp, i))
				continue;

			if (slot.GetAttachedEntity() == chosen)
				return slot;

			if (!fallback)
				fallback = slot;
		}
		return fallback;
	}

	// Accessories that go onto worn gear rather than the body (a helmet light,
	// NVGs on a helmet mount, a flashlight on a vest) go straight into a free
	// spot for them on what's worn. Pockets and backpacks don't count, so
	// everything else still goes in the bag as usual.
	protected bool OYB_EquipAccessory(string via)
	{
		SCR_ArsenalInventorySlotUI arsenalSlot = SCR_ArsenalInventorySlotUI.Cast(m_pSelectedSlotUI);
		if (!arsenalSlot || !arsenalSlot.IsAvailable() || !arsenalSlot.GetInventoryItemComponent() || !m_InventoryManager)
			return false;

		IEntity preview = arsenalSlot.GetInventoryItemComponent().GetOwner();
		if (!preview || preview.FindComponent(BaseLoadoutClothComponent) || preview.FindComponent(BaseWeaponComponent) || preview.FindComponent(BaseMagazineComponent))
			return false;

		SCR_CharacterInventoryStorageComponent wearing = m_InventoryManager.GetCharacterStorage();
		if (!wearing)
			return false;

		// What's worn, and what's already mounted on it (NVGs can go on a mount
		// that's on the helmet).
		BaseInventoryStorageComponent target;
		array<IEntity> worn = {};
		wearing.GetAll(worn);
		int wornCount = worn.Count();
		for (int i = 0; i < wornCount; i++)
		{
			if (worn[i])
				OYB_GetMounted(worn[i], worn);
		}
		foreach (IEntity piece : worn)
		{
			target = OYB_FindAccessorySpot(piece, preview);
			if (target)
				break;
		}

		// No free spot: one already holding an accessory of this kind gets it
		// swapped, the way clothing is.
		InventoryStorageSlot taken;
		if (!target)
		{
			foreach (IEntity wornPiece : worn)
			{
				taken = OYB_FindTakenAccessorySpot(wornPiece, preview);
				if (taken)
					break;
			}
			if (!taken)
			{
				OYB_ReportNoSpot(worn, preview, arsenalSlot.GetItemResource());
				return false;
			}
		}

		IEntity arsenalEntity = SCR_InventoryStorageBaseUI.ARSENAL_SLOT_STORAGES.Get(arsenalSlot);
		SCR_ResourceComponent resourceComponent = SCR_ResourceComponent.FindResourceComponent(arsenalEntity);
		if (!resourceComponent)
			return false;

		RplId resourceId = Replication.FindItemId(resourceComponent);
		ResourceName resource = arsenalSlot.GetItemResource();
		if (target)
		{
			PrintFormat("OYB_ARSENAL accessory (%1): putting %2 on worn gear", via, resource.GetPath());
			OYB_ArsenalRequest.Request(resourceId, Replication.FindItemId(target), resource);
			return true;
		}

		IEntity old = taken.GetAttachedEntity();
		BaseInventoryStorageComponent spot = taken.GetStorage();
		PrintFormat("OYB_ARSENAL accessory (%1): taking off %2 to put on %3", via, FilePath.StripPath(old.GetPrefabData().GetPrefabName().GetPath()), resource.GetPath());
		if (!m_InventoryManager.TryRemoveItemFromStorage(old, spot))
		{
			Print("OYB_ARSENAL the old accessory can't be taken off, nothing changed");
			SCR_UISoundEntity.SoundEvent(SCR_SoundEvent.SOUND_INV_DROP_ERROR);
			return true;
		}

		OYB_PendingSwap.Start(m_InventoryManager, spot, taken, old, resourceId, resource, SCR_ArsenalComponent.FindArsenalComponent(arsenalEntity), false);
		return true;
	}

	protected InventoryStorageSlot OYB_FindTakenAccessorySpot(IEntity piece, IEntity accessory)
	{
		if (!piece)
			return null;

		array<Managed> found = {};
		piece.FindComponents(BaseInventoryStorageComponent, found);
		foreach (Managed component : found)
		{
			BaseInventoryStorageComponent storage = BaseInventoryStorageComponent.Cast(component);
			if (!OYB_IsMount(storage))
				continue;

			InventoryStorageSlot slot = storage.FindSuitableSlotForItem(accessory);
			if (slot && slot.GetAttachedEntity() && slot.GetAttachedEntity().GetPrefabData())
				return slot;
		}
		return null;
	}

	// For testing on modded gear: what each worn piece can hold, when an item
	// found no place on any of them.
	protected void OYB_ReportNoSpot(notnull array<IEntity> worn, IEntity item, ResourceName resource)
	{
		OYB_ArsenalLogLine(string.Format("accessory: no place for %1 on worn gear, checked:", FilePath.StripPath(resource.GetPath())));
		foreach (IEntity piece : worn)
		{
			if (!piece || !piece.GetPrefabData())
				continue;

			string line = FilePath.StripPath(piece.GetPrefabData().GetPrefabName().GetPath()) + ":";
			array<Managed> found = {};
			piece.FindComponents(BaseInventoryStorageComponent, found);
			foreach (Managed component : found)
			{
				BaseInventoryStorageComponent storage = BaseInventoryStorageComponent.Cast(component);
				if (!storage)
					continue;

				string fits = "no";
				if (m_InventoryManager.CanInsertItemInStorage(item, storage, -1))
					fits = "yes";
				else if (storage.FindSuitableSlotForItem(item))
					fits = "taken";
				line += string.Format(" %1(%2, fits %3)", storage.ClassName(), typename.EnumToString(EStoragePurpose, storage.GetPurpose()), fits);
			}

			array<Managed> slots = {};
			piece.FindComponents(AttachmentSlotComponent, slots);
			if (!slots.IsEmpty())
				line += string.Format(" %1 attachment slots", slots.Count());
			OYB_ArsenalLogLine(line);
		}
	}

	protected void OYB_ArsenalLogLine(string text)
	{
		Print("OYB_ARSENAL " + text);
	}

	// A storage that holds something on a piece of gear rather than in a pocket:
	// the game's equipment and cloth node storages, or any storage set up for
	// attachments or gadgets (how a helmet's NVG mount is often made).
	protected bool OYB_IsMount(BaseInventoryStorageComponent storage)
	{
		if (!storage)
			return false;
		if (BaseEquipmentStorageComponent.Cast(storage) || ClothNodeStorageComponent.Cast(storage) || SCR_FilteredInventoryStorageComponent.Cast(storage))
			return true;

		EStoragePurpose purpose = storage.GetPurpose();
		return purpose == EStoragePurpose.PURPOSE_EQUIPMENT_ATTACHMENT || purpose == EStoragePurpose.PURPOSE_GADGET_PROXY || purpose == EStoragePurpose.PURPOSE_ATTACHMENT_PROXY;
	}

	protected void OYB_GetMounted(IEntity piece, notnull array<IEntity> mounted)
	{
		array<Managed> found = {};
		piece.FindComponents(BaseInventoryStorageComponent, found);
		foreach (Managed component : found)
		{
			BaseInventoryStorageComponent storage = BaseInventoryStorageComponent.Cast(component);
			if (!OYB_IsMount(storage))
				continue;

			array<IEntity> items = {};
			storage.GetAll(items, false);
			foreach (IEntity item : items)
			{
				if (item && !mounted.Contains(item))
					mounted.Insert(item);
			}
		}

		array<Managed> slots = {};
		piece.FindComponents(AttachmentSlotComponent, slots);
		foreach (Managed slotComponent : slots)
		{
			AttachmentSlotComponent slot = AttachmentSlotComponent.Cast(slotComponent);
			if (slot && slot.GetAttachedEntity() && !mounted.Contains(slot.GetAttachedEntity()))
				mounted.Insert(slot.GetAttachedEntity());
		}
	}

	// A free spot for the accessory on this worn piece: an equipment or cloth node
	// storage, or an attachment slot, that takes it.
	protected BaseInventoryStorageComponent OYB_FindAccessorySpot(IEntity piece, IEntity accessory)
	{
		if (!piece)
			return null;

		array<Managed> found = {};
		piece.FindComponents(BaseInventoryStorageComponent, found);
		foreach (Managed component : found)
		{
			BaseInventoryStorageComponent storage = BaseInventoryStorageComponent.Cast(component);
			if (!storage || !m_InventoryManager.CanInsertItemInStorage(accessory, storage, -1))
				continue;
			if (OYB_IsMount(storage))
				return storage;

			// Seen in testing so a mod's own mount can be recognised.
			string pieceName;
			if (piece.GetPrefabData())
				pieceName = FilePath.StripPath(piece.GetPrefabData().GetPrefabName().GetPath());
			PrintFormat("OYB_ARSENAL accessory: %1 on %2 takes it but isn't counted as a mount (purpose %3)", storage.ClassName(), pieceName, typename.EnumToString(EStoragePurpose, storage.GetPurpose()));
		}

		array<Managed> slots = {};
		piece.FindComponents(AttachmentSlotComponent, slots);
		foreach (Managed slotComponent : slots)
		{
			AttachmentSlotComponent slot = AttachmentSlotComponent.Cast(slotComponent);
			if (slot && !slot.GetAttachedEntity() && slot.CanSetAttachment(accessory))
				return OYB_TargetWeapon.GetSlotStorage(piece);
		}
		return null;
	}

	// See OYB_ArsenalClothing.c. Anything else with its own place on the body
	// (NVGs in a night vision slot, a watch, binoculars) goes on the same way.
	protected bool OYB_EquipClothing(string via)
	{
		SCR_ArsenalInventorySlotUI arsenalSlot = SCR_ArsenalInventorySlotUI.Cast(m_pSelectedSlotUI);
		if (!arsenalSlot || !arsenalSlot.IsAvailable() || !arsenalSlot.GetInventoryItemComponent())
			return false;

		IEntity preview = arsenalSlot.GetInventoryItemComponent().GetOwner();
		if (!preview || preview.FindComponent(BaseWeaponComponent) || preview.FindComponent(BaseMagazineComponent) || !m_InventoryManager)
			return false;

		SCR_CharacterInventoryStorageComponent wearing = m_InventoryManager.GetCharacterStorage();
		if (!wearing)
			return false;

		InventoryStorageSlot slot = wearing.FindSuitableSlotForItem(preview);
		if (!slot)
			return false;

		IEntity arsenalEntity = SCR_InventoryStorageBaseUI.ARSENAL_SLOT_STORAGES.Get(arsenalSlot);
		SCR_ResourceComponent resourceComponent = SCR_ResourceComponent.FindResourceComponent(arsenalEntity);
		if (!resourceComponent)
			return false;

		RplId resourceId = Replication.FindItemId(resourceComponent);
		ResourceName resource = arsenalSlot.GetItemResource();

		IEntity worn = slot.GetAttachedEntity();
		if (!worn)
		{
			// The server takes the supplies before it spawns, so don't ask for
			// something that can't go in.
			if (!m_InventoryManager.CanInsertItemInStorage(preview, wearing, slot.GetID()))
			{
				OYB_ArsenalLogLine(string.Format("wear (%1): %2 has a body slot (%3) but the game won't put it there", via, FilePath.StripPath(arsenalSlot.GetItemResource().GetPath()), slot.GetID()));
				return false;
			}

			PrintFormat("OYB_ARSENAL wear (%1): slot empty, requesting %2", via, resource.GetPath());
			OYB_ArsenalRequest.Request(resourceId, Replication.FindItemId(wearing), resource);
			return true;
		}

		PrintFormat("OYB_ARSENAL wear (%1): taking off %2 to put on %3", via, FilePath.StripPath(worn.GetPrefabData().GetPrefabName().GetPath()), resource.GetPath());
		if (!m_InventoryManager.TryRemoveItemFromStorage(worn, slot.GetStorage()))
		{
			Print("OYB_ARSENAL the worn piece can't be taken off, nothing changed");
			SCR_UISoundEntity.SoundEvent(SCR_SoundEvent.SOUND_INV_DROP_ERROR);
			return true;
		}

		OYB_PendingSwap.Start(m_InventoryManager, wearing, slot, worn, resourceId, resource, SCR_ArsenalComponent.FindArsenalComponent(arsenalEntity), true);
		return true;
	}
}

// The game's own arsenal request, as used when an item is dragged out of an
// arsenal. A plain class rather than part of the modded menu, so every OYB file
// can call it whatever order the files are compiled in.
class OYB_ArsenalRequest
{
	static void Request(RplId resourceId, RplId storageId, ResourceName resource)
	{
		SCR_ResourcePlayerControllerInventoryComponent requests = SCR_ResourcePlayerControllerInventoryComponent.Cast(GetGame().GetPlayerController().FindComponent(SCR_ResourcePlayerControllerInventoryComponent));
		if (requests)
			requests.RpcAsk_ArsenalRequestItem(resourceId, storageId, resource, EResourceType.SUPPLIES);
	}
}

// Waits for a refunded part to leave its slot, then asks for the new one. Kept
// apart from the menu so closing the inventory mid-wait is harmless.
class OYB_PendingFit
{
	protected static const int CHECK_EVERY_MS = 100;
	protected static const int GIVE_UP_AFTER_CHECKS = 30;

	protected static ref OYB_PendingFit s_Pending;

	protected AttachmentSlotComponent m_Slot;
	protected RplId m_ResourceId;
	protected RplId m_StorageId;
	protected ResourceName m_Resource;
	protected int m_iChecks;

	static void Start(AttachmentSlotComponent slot, RplId resourceId, RplId storageId, ResourceName resource)
	{
		if (s_Pending)
			s_Pending.Stop();

		s_Pending = new OYB_PendingFit();
		s_Pending.m_Slot = slot;
		s_Pending.m_ResourceId = resourceId;
		s_Pending.m_StorageId = storageId;
		s_Pending.m_Resource = resource;
		GetGame().GetCallqueue().CallLater(s_Pending.Check, CHECK_EVERY_MS, true);
	}

	protected void Check()
	{
		m_iChecks++;
		if (!m_Slot)
		{
			Stop();
			return;
		}

		if (!m_Slot.GetAttachedEntity())
		{
			Print("OYB_ARSENAL fit: old part is off, requesting the new one");
			OYB_ArsenalRequest.Request(m_ResourceId, m_StorageId, m_Resource);
			Stop();
			return;
		}

		if (m_iChecks >= GIVE_UP_AFTER_CHECKS)
		{
			Print("OYB_ARSENAL the arsenal didn't take the old part back, nothing new was requested");
			SCR_UISoundEntity.SoundEvent(SCR_SoundEvent.SOUND_INV_DROP_ERROR);
			Stop();
		}
	}

	protected void Stop()
	{
		GetGame().GetCallqueue().Remove(Check);
		if (s_Pending == this)
			s_Pending = null;
	}
}
