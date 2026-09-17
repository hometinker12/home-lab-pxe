from src.nfs_media import advertised_nfsroot, nfs_subpath


def test_nfs_subpath_accepts_generation():
    assert nfs_subpath("nfs/3/1") == "3/1"
    assert nfs_subpath(r"nfs\3\1") == "3/1"
    assert nfs_subpath("uploads/3/extracts/1") is None
    assert nfs_subpath("nfs/../etc") is None
    assert nfs_subpath("nfs/3/1/extra") is None


def test_advertised_nfsroot_uses_container_export():
    root = advertised_nfsroot("nfs/3/1", host="192.168.2.223", export="/var/lib/pxe/images/nfs")
    assert root == "192.168.2.223:/var/lib/pxe/images/nfs/3/1"


def test_advertised_nfsroot_rejects_windows_export():
    assert advertised_nfsroot("nfs/3/1", host="pxe.test", export="C:/tmp/nfs") is None
    assert advertised_nfsroot("nfs/3/1", host="pxe.test", export="/var/lib/pxe/images/../etc") is None
