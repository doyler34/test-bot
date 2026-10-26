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
// the player, the distance, the arsenal and the supply cost. Magazines and
// everything else keep the vanilla behaviour.
modded class SCR_InventoryMenuUI
{
	override protected bool MoveBetweenFromVicinity_VirtualArsenal()
	{
		if (OYB_FitToHeldWeapon("quick move") || OYB_EquipClothing("quick move"))
			return true;

		return super.MoveBetweenFromVicinity_VirtualArsenal();
	}

	override protected bool EquipDraggedItem_VirtualArsenal(bool forceEquip = false)
	{
		if (OYB_FitToHeldWeapon("drag onto character") || OYB_EquipClothing("drag onto character"))
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
			bool fitted = OYB_FitToHeldWeapon("equip") || OYB_EquipClothing("equip");
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
		WeaponAttachmentsStorageComponent weaponStorage = WeaponAttachmentsStorageComponent.Cast(weaponEntity.FindComponent(WeaponAttachmentsStorageComponent));
		if (!weapon || !weaponStorage)
		{
			PrintFormat("OYB_ARSENAL fit (%1): the held weapon has no attachment storage", via);
			return false;
		}

		array<AttachmentSlotComponent> slots = {};
		weapon.GetAttachments(slots);
		AttachmentSlotComponent target;
		foreach (AttachmentSlotComponent slot : slots)
		{
			if (slot && slot.CanSetAttachment(item))
			{
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
		RplId storageId = Replication.FindItemId(weaponStorage);
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


	protected bool OYB_EquipClothing(string via)
	{
		SCR_ArsenalInventorySlotUI arsenalSlot = SCR_ArsenalInventorySlotUI.Cast(m_pSelectedSlotUI);
		if (!arsenalSlot || !arsenalSlot.IsAvailable() || !arsenalSlot.GetInventoryItemComponent())
			return false;

		IEntity preview = arsenalSlot.GetInventoryItemComponent().GetOwner();
		if (!preview || !preview.FindComponent(BaseLoadoutClothComponent) || !m_InventoryManager)
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

		OYB_PendingClothing.Start(m_InventoryManager, wearing, slot, worn, resourceId, resource, SCR_ArsenalComponent.FindArsenalComponent(arsenalEntity));
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
