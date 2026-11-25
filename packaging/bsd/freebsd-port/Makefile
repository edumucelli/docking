PORTNAME=	docking
DISTVERSIONPREFIX=	v
DISTVERSION=	2.35.0
CATEGORIES=	x11

MAINTAINER=	hello@docking.cc
COMMENT=	Desktop dock with launchers, window previews, and applets
WWW=		https://docking.cc/

LICENSE=	GPLv3+
LICENSE_FILE=	${WRKSRC}/LICENSE

BUILD_DEPENDS=	bash:shells/bash
RUN_DEPENDS=	${PYTHON_PKGNAMEPREFIX}pycairo>0:graphics/py-pycairo@${PY_FLAVOR} \
		${PYTHON_PKGNAMEPREFIX}pygobject>0:devel/py-pygobject@${PY_FLAVOR} \
		${PYTHON_PKGNAMEPREFIX}psutil>=6:sysutils/py-psutil@${PY_FLAVOR} \
		dbus-run-session:devel/dbus \
		libXScrnSaver>0:x11/libXScrnSaver \
		pkexec:sysutils/polkit \
		xterm:x11/xterm

USES=		gettext-tools gnome python:3.11+
USE_GITHUB=	yes
GH_ACCOUNT=	edumucelli
USE_GNOME=	gtk30 libwnck3

NO_ARCH=	yes
SUB_FILES=	docking docking-camshield-helper
SUB_LIST=	PYTHON_CMD=${PYTHON_CMD}

PLIST_FILES=	bin/docking \
		bin/docking-camshield-helper \
		share/applications/org.docking.Docking.desktop \
		share/polkit-1/actions/org.docking.camshield.policy

do-build:
	${SETENV} ${MAKE_ENV} ${LOCALBASE}/bin/bash ${WRKSRC}/tools/i18n.sh --compile

do-install:
	${MKDIR} ${STAGEDIR}${PREFIX}/bin \
		${STAGEDIR}${PREFIX}/lib/docking/python \
		${STAGEDIR}${PREFIX}/share/applications \
		${STAGEDIR}${PREFIX}/share/icons/hicolor \
		${STAGEDIR}${PREFIX}/share/polkit-1/actions
	cd ${WRKSRC} && ${COPYTREE_SHARE} docking ${STAGEDIR}${PREFIX}/lib/docking/python
	cd ${WRKSRC}/packaging/deb/icons/hicolor && \
		${COPYTREE_SHARE} . ${STAGEDIR}${PREFIX}/share/icons/hicolor
	${INSTALL_SCRIPT} ${WRKDIR}/docking ${WRKDIR}/docking-camshield-helper \
		${STAGEDIR}${PREFIX}/bin
	${INSTALL_DATA} ${WRKSRC}/packaging/shared/org.docking.Docking.desktop \
		${STAGEDIR}${PREFIX}/share/applications
	${SED} -e 's|/usr/bin/docking-camshield-helper|${PREFIX}/bin/docking-camshield-helper|' \
		${WRKSRC}/packaging/shared/org.docking.camshield.policy > ${WRKDIR}/org.docking.camshield.policy
	${INSTALL_DATA} ${WRKDIR}/org.docking.camshield.policy ${STAGEDIR}${PREFIX}/share/polkit-1/actions

post-install:
	cd ${STAGEDIR}${PREFIX} && ${FIND} -s lib/docking share/icons/hicolor -type f \
		>> ${TMPPLIST}

.include <bsd.port.mk>
